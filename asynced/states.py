from __future__ import annotations

__all__ = ('StateVar', 'StateVarTuple')

import abc
import asyncio
import collections
import inspect
import sys
from typing import (
    Any,
    AsyncIterable,
    AsyncIterator,
    Awaitable,
    Callable,
    Coroutine,
    Generator,
    Generic,
    Hashable,
    Iterable,
    Literal,
    NoReturn,
    overload,
    Sequence,
    SupportsIndex,
    TypeVar,
)
from typing_extensions import TypeAlias

from ._states import SimpleStateBase, SimpleStateValue
from ._typing import Maybe, Nothing
from .asyncio_utils import create_future, race
from .exceptions import StateError, StopAnyIteration

_T = TypeVar('_T')
_KT = TypeVar('_KT', bound=SupportsIndex)

_S = TypeVar('_S', bound=Hashable)
_RS = TypeVar('_RS', bound=Hashable)


AwaitablePredicate: TypeAlias = SimpleStateValue[bool]


def __intern_predicate_keys():
    for key in ('done', 'stop', 'set', 'error', 'cancel'):
        sys.intern(key)


__intern_predicate_keys()


class _PredicatesMixin:
    __slots__ = ()

    _predicates: dict[str, AwaitablePredicate]

    @property
    def is_set(self) -> AwaitablePredicate:
        return self._predicates['set']

    @property
    def is_done(self) -> AwaitablePredicate:
        return self._predicates['done']

    @property
    def is_stopped(self) -> AwaitablePredicate:
        """StopIteration or StopAsyncIteration was raised"""
        return self._predicates['stop']

    @property
    def is_error(self) -> AwaitablePredicate:
        return self._predicates['error']

    @property
    def is_cancelled(self) -> AwaitablePredicate:
        return self._predicates['cancel']

    def _set_predicate(self, **kwargs: bool) -> None:
        for key, value in kwargs.items():
            predicate = self._predicates[key]
            if not predicate.as_future().done():
                predicate.set(value)

    @abc.abstractmethod
    def _on_set(self): ...
    @abc.abstractmethod
    def _on_error(self, exc: BaseException): ...
    @abc.abstractmethod
    def _on_cancel(self): ...
    @abc.abstractmethod
    def _on_stop(self): ...

    def _update(self, future: asyncio.Future[_S]) -> None:
        assert future.done()

        if future.cancelled():
            self._on_cancel()
        elif (exc := future.exception()) is not None:
            if isinstance(exc, asyncio.CancelledError):
                self._on_cancel()
            elif isinstance(exc, StopAnyIteration):
                self._on_stop()
            else:
                self._on_error(exc)
        else:
            self._on_set()


class StateValue(_PredicatesMixin, SimpleStateValue[_S], Generic[_S]):
    __slots__ = ('_predicates', )

    _predicates: dict[str, AwaitablePredicate]

    def __init__(
        self,
        coro: Maybe[asyncio.Future[_T] | Coroutine[Any, Any, _T]] = Nothing
    ) -> None:
        self._predicates = collections.defaultdict(SimpleStateValue)

        super().__init__(coro)

    def _on_set(self):
        self.__on_done('set')

    def _on_stop(self):
        self.__on_done('stop')

    def _on_error(self, exc: BaseException):
        self.__on_done('error')

    def _on_cancel(self):
        self.__on_done('cancel')

    def __on_done(self, key: Literal['set', 'error', 'cancel', 'stop']) -> None:
        keys = ['set', 'stop', 'error', 'cancel']
        self._set_predicate(done=True, **{k: k == key for k in keys})


class StateVarBase(
    _PredicatesMixin,
    AsyncIterator[_S],
    SimpleStateBase[_S],
    Generic[_S]
):
    __slots__ = ('_producer', '_value', '_value_next', '_predicates')

    _predicates: dict[str, AwaitablePredicate]

    _producer: Maybe[AsyncIterator[_S]]

    _value: StateValue[_S]
    _value_next: StateValue[_S]

    def __init__(
        self,
        producer: Maybe[StateVarBase[_S] | AsyncIterable[_S]] = Nothing
    ) -> None:
        self._predicates = collections.defaultdict(SimpleStateValue)

        if producer is not Nothing:
            producer = producer.__aiter__()
        self._producer = producer

        self._value = self.__schedule_next()

        super().__init__()

    def __await__(self) -> Generator[Any, None, _S]:
        return self._value.__await__()

    def __aiter__(self: _S) -> AsyncIterator[_S]:
        async def _aiter():
            prev = default = object()
            try:
                while True:
                    current = self.get(default)

                    if current is prev or current == prev:
                        current = await self._value_next

                        if current is prev or current == prev:
                            continue

                        yield current
                        prev = current
                        continue

                    yield current
                    prev = current
                    await asyncio.sleep(0)

            except (StopIteration, StopAsyncIteration, asyncio.CancelledError):
                pass

        return _aiter()

    async def __anext__(self) -> _S:
        try:
            return await self.next()
        except asyncio.CancelledError as exc:
            raise StopAsyncIteration from exc

    def as_future(self) -> asyncio.Future[_S]:
        return self._value_next.as_future()

    def get(self, default: Maybe[_T] = Nothing) -> _S | _T:
        """Return the current state value.

        If not set, the method will return the default value of the `default`
        argument of the method, if provided; or raise a LookupError.
        """
        fut = self._value.as_future()
        if fut.done():
            try:
                return fut.result()
            except (StopAsyncIteration, asyncio.CancelledError):
                if default is not Nothing:
                    return default
                raise

        if default is not Nothing:
            return default
        raise LookupError(repr(self))

    def next(self) -> StateValue[_S]:
        """Returns a state value that has no value set yet, so that awaiting it
        will return when the next / future state is set.

        If currently there is no value, this state variable is returned,
        otherwise a new state variable
        """
        value = self._value
        fut = value.as_future()
        if fut.done():
            fut.result()  # will raise if cancelled or raised
        else:
            return value

        value_next = self._value_next
        fut_next = value_next.as_future()
        if fut_next.done():
            res = fut_next.result()
            raise StateError(f'the next value has been set (!?): {res!r}')
        return value_next

    def set(self, state: _S) -> bool:
        """Set the new state. If the state is equal to the current state,
        false is returned. Otherwise, the state is set, the waiters will
        be notified, and true is returned.

        Raises StateError if pending.

        Not threadsafe.
        """
        if state is Nothing:
            raise TypeError('cannot set state to nothing')
        if self.readonly:
            raise StateError(f'{self!r} is read-only')
        if self._value_next.is_done:
            raise StateError(f'{self!r} is done')

        default = object()
        if (current := self.get(default)) is not default:
            if state is current or state == current:
                return False

        value_next = self._value_next
        value_next.set(state)

        self.__schedule_next()
        self._value = value_next

        self._update(value_next.as_future())

        return True

    def map(
        self,
        function: Callable[[_S], _RS] | Callable[[_S], Awaitable[_RS]],
    ) -> StateVar[_RS]:
        async def producer() -> AsyncIterator[_RS]:
            is_async = None
            if asyncio.iscoroutinefunction(function):
                is_async = True

            async for state in self:
                res = function(state)
                if is_async is None:
                    is_async = inspect.isawaitable(res)

                yield (await res) if is_async else res

        return type(self)(producer())

    def _format(self) -> str:
        # noinspection PyProtectedMember
        return self._value._format()

    def __schedule_next(self) -> StateValue[_S]:
        # assert not hasattr(self, '_value_next') or self._value_next.is_set

        if self._producer is Nothing:
            value_next = self._value_next = StateValue()
            return value_next

        # Chicken & Egg Problem: Solved
        feathery_wormhole: asyncio.Future[StateValue[_S]]
        feathery_wormhole = create_future()

        loop = asyncio.get_running_loop()

        is_initial = not hasattr(self, '_value')

        async def _hatch():
            # Wait for the egg, but don't touch it! Unless you want spacetime
            # to fold into a singularity...
            interdimensional_egg: StateValue[_S] = await feathery_wormhole

            chick_senpai = Nothing
            if not is_initial:
                try:
                    chick_senpai = self.get()
                except LookupError:
                    pass

            producer = self._producer
            try:
                chick: _S = await producer.__anext__()

                # strive for chick diversity; skip identical twins
                if chick_senpai:
                    while chick is chick_senpai or chick == chick_senpai:
                        chick: _S = await producer.__anext__()
            except (StopIteration, StopAsyncIteration, asyncio.CancelledError):
                # The chicken mom went to the ctrl+Z clinic
                raise
            except (KeyboardInterrupt, SystemExit):
                # Universe is imploding; raise ASAP
                raise
            except BaseException:  # noqa
                # *The egg is a Lie* - make both __await__ and __anext__ raise
                self._value = interdimensional_egg
                raise
            else:
                # success! what was the future is now the present, and the
                # future is beyond the future of the past
                assert self._value_next is interdimensional_egg

                # repeat and rinse (:
                try:
                    loop.call_soon(self.__schedule_next)
                except RuntimeError:
                    pass

                return chick
                # the interdimensional egg is hatched

        egg = StateValue(_hatch())
        egg.as_future().add_done_callback(self._update)

        if is_initial:
            self._value = self._value_next = egg
        else:
            self._value, self._value_next = self._value_next, egg

        feathery_wormhole.set_result(egg)
        return egg

    @abc.abstractmethod
    def _on_set(self): ...
    @abc.abstractmethod
    def _on_stop(self): ...
    @abc.abstractmethod
    def _on_error(self, exc: BaseException): ...
    @abc.abstractmethod
    def _on_cancel(self): ...


class StateVar(StateVarBase[_S], _PredicatesMixin, Generic[_S]):
    __slots__ = ()

    def split(self: StateVar[Sequence[_RS, ...]]) -> StateVarTuple[_RS]:
        # TODO docstring
        n = len(self.get())

        async def producer(i: int):
            async for state in self:
                yield state[i]

        return StateVarTuple(map(producer, range(n)))

    def _on_set(self):
        self._set_predicate(set=True)

    def _on_error(self, exc: BaseException):
        self._set_predicate(done=True, error=True, cancel=False, stop=False)

    def _on_cancel(self):
        self._set_predicate(done=True, error=False, cancel=True, stop=False)

    def _on_stop(self):
        self._set_predicate(done=True, error=False, cancel=False, stop=True)


class StateVarTuple(
    Sequence[StateVar[_S]],
    StateVarBase[tuple[_S, ...]],
    Generic[_S],
):
    __slots__ = ('_statevars', )

    _statevars: tuple[StateVarBase[_S], ...]

    def __init__(
        self,
        producers: int | Iterable[StateVarBase[_S] | AsyncIterable[_S]],
        statevar_type: type[StateVarBase] = StateVar
    ) -> None:
        if isinstance(producers, int):
            self._statevars = tuple(
                statevar_type() for _ in range(producers)
            )
        else:
            self._statevars = tuple(
                prod if isinstance(prod, StateVarBase) else statevar_type(prod)
                for prod in producers
            )
        if not self._statevars:
            raise TypeError('StateVarTuple() must have at least one producer.')

        super().__init__(self._get_producer())

    def __await__(self) -> Generator[Any, None, tuple[_S, ...]]:
        return self._gather().__await__()

    @overload
    def __getitem__(self, i: int) -> StateVarBase[_S]: ...

    @overload
    def __getitem__(self, s: slice) -> StateVarTuple[_S]: ...

    def __getitem__(self, i: int | slice) -> StateVarBase[_S]:
        if isinstance(i, int):
            return self._statevars[i]
        elif isinstance(i, slice):
            return type(self)(self._statevars[i])
        else:
            raise TypeError(
                f'{type(self).__name__} indices must be integers or slices, '
                f'not {type(i).__name__}'
            )

    def __setitem__(self, i: int, state: _S) -> None:
        self._statevars[i].set(state)

    def __contains__(self, item: object) -> bool:
        if isinstance(item, StateVarBase):
            return item in self._statevars

        default = object()
        return item in tuple(s for s in self.get(default) if s is not default)

    def __len__(self) -> int:
        return len(self._statevars)

    def __reversed__(self) -> StateVarTuple[_S]:
        return self[::-1]

    def __add__(self, other: StateVarTuple[_S]):
        cls = type(self)
        if not isinstance(other, StateVarTuple):

            raise TypeError(
                f'can only concatenate {cls.__name__} (not '
                f'{type(other).__name__!r}) to {cls.__name__}'
            )

        return cls(self._statevars + other._statevars)

    def __mul__(self, value: int) -> StateVarTuple[_S]:
        """Return self * value"""
        if not isinstance(value, int):
            raise TypeError(
                f'can\'t multiply {type(self).__name__} by non-int of type '
                f'{type(value).__name__}'
            )
        return type(self)(self._statevars * value)

    def __rmul__(self, value: int) -> StateVarTuple[_S]:
        """Return value * self"""
        return self.__mul__(value)

    def __hash__(self) -> int:
        return hash(self._statevars)

    @property
    def readonly(self) -> bool:
        """.set() cannot be used, but unless the statevar items are readable,
        they can be set with e.g. self[0] = ...
        """
        return True

    @property
    def all_set(self) -> AwaitablePredicate:
        return self._predicates['n_set']

    @property
    def all_done(self) -> AwaitablePredicate:
        return self._predicates['n_done']

    @property
    def all_stopped(self) -> AwaitablePredicate:
        return self._predicates['n_stop']

    @property
    def all_error(self) -> AwaitablePredicate:
        return self._predicates['n_error']

    @property
    def all_cancelled(self) -> AwaitablePredicate:
        return self._predicates['n_cancel']

    def get(self, default: Maybe[_T] = Nothing) -> tuple[_S | _T, ...]:
        return tuple(sv.get(default) for sv in self._statevars)

    def set(self, state: tuple[_S, ...]) -> NoReturn:
        raise StateError(f'{type(self).__name__!r} cannot be set directly')

    def starmap(
        self,
        function: Callable[..., _RS] | Callable[..., Awaitable[_RS]],
    ) -> StateVar[_RS]:
        return self.map(lambda args: function(*args))

    async def _gather(self) -> tuple[_S, ...]:
        return tuple(await asyncio.gather(*self._statevars))

    async def _get_producer(self) -> AsyncIterator[tuple[_S]]:
        states = await self._gather()
        yield states

        async for i, state in race(*self._statevars):
            states = states[0:i] + (state, ) + states[i+1:]
            yield states

    def _on_set(self):
        if self._predicates['n_set'].as_future().done():
            return

        self._set_predicate(set=True)

        if (n_set := self.__is_all_predicates('set')) is not None:
            self._set_predicate(n_set=n_set)

    def _on_stop(self):
        self._set_predicate(stop=True)

        if (n_stop := self.__is_all_predicates('stop')) is not None:
            self._set_predicate(n_error=False, n_cancel=False, n_stop=n_stop)
            self.__on_all_done()

    def _on_error(self, exc: BaseException):
        self._set_predicate(error=True)

        if (n_error := self.__is_all_predicates('error')) is not None:
            self._set_predicate(n_error=n_error, n_cancel=False, n_stop=False)
            self.__on_all_done()

    def _on_cancel(self):
        self._set_predicate(cancel=True)

        if (n_cancel := self.__is_all_predicates('cancel')) is not None:
            self._set_predicate(n_error=False, n_cancel=n_cancel, n_stop=False)
            self.__on_all_done()

    def __on_all_done(self):
        self._set_predicate(n_done=True)
        if not self._predicates['n_set'].as_future().done():
            self._set_predicate(n_set=False)

    def __is_all_predicates(self, key: str) -> bool | None:
        is_all = True
        for statevar in self:
            # noinspection PyProtectedMember
            predicates = statevar._predicates
            if key not in predicates:
                return None

            predicate = predicates[key]
            future = predicate.as_future()
            if not future.done():
                return None

            is_all = is_all and future.result()

        return is_all
