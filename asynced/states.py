from __future__ import annotations

__all__ = ('StateVar', 'StateVarTuple')

import abc
import asyncio
import collections
import inspect
from typing import (
    Any, AsyncIterable,
    AsyncIterator,
    Awaitable, Callable, Coroutine, Generator,
    Generic,
    Hashable,
    Literal,
    NoReturn,
    overload,
    Sequence,
    TypeVar,
)
from typing_extensions import Self, TypeAlias

from ._states import SimpleStateBase, SimpleStateValue
from ._typing import Maybe, Nothing
from .asyncio_utils import create_future, race
from .exceptions import StateError, StopAnyIteration

_T = TypeVar('_T')

_S = TypeVar('_S', bound=Hashable)
_RS = TypeVar('_RS', bound=Hashable)


AwaitablePredicate: TypeAlias = SimpleStateValue[bool]


class _PredicatesMixin:
    _predicates: dict[str, AwaitablePredicate]

    @property
    def is_done(self) -> AwaitablePredicate:
        return self._predicates['done']

    @property
    def is_set(self) -> AwaitablePredicate:
        return self._predicates['set']

    @property
    def is_error(self) -> AwaitablePredicate:
        return self._predicates['error']

    @property
    def is_cancelled(self) -> AwaitablePredicate:
        return self._predicates['cancel']

    @property
    def is_stopped(self) -> AwaitablePredicate:
        """StopIteration or StopAsyncIteration was raised"""
        return self._predicates['stop']

    def _set_predicate(self, **kwargs: bool) -> None:
        for key, value in kwargs.items():
            predicate = self._predicates[key]
            if not predicate.as_future().done():
                predicate.set(value)

    @abc.abstractmethod
    def _on_set(self, _: _S): ...
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
            self._on_set(future.result())


class StateValue(_PredicatesMixin, SimpleStateValue[_S], Generic[_S]):
    __slots__ = ('_predicates', )

    _predicates: dict[str, AwaitablePredicate]

    def __init__(
        self,
        coro: Maybe[asyncio.Future[_T] | Coroutine[Any, Any, _T]] = Nothing
    ) -> None:
        self._predicates = collections.defaultdict(SimpleStateValue)

        super().__init__(coro)

    def _on_done(self, key: Literal['set', 'error', 'cancel', 'stop']) -> None:
        keys = ['set', 'stop', 'error', 'cancel']
        self._set_predicate(done=True, **{k: k == key for k in keys})

    def _on_set(self, _: _S):
        self._on_done('set')

    def _on_stop(self):
        self._on_done('stop')

    def _on_error(self, exc: BaseException):
        self._on_done('error')

    def _on_cancel(self):
        self._on_done('cancel')


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
            try:
                yield await self

                while True:
                    yield await self._value_next

            except (StopAsyncIteration, asyncio.CancelledError):
                pass

        return _aiter()

    async def __anext__(self) -> _S:
        try:
            return await self._value_next
        except asyncio.CancelledError:
            raise StopAsyncIteration

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
        if not self._value.as_future().done():
            return self._value
        return self._value_next

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

        value, value_next = self._value, self._value_next

        assert not value_next.is_set

        if state == value:
            return False

        self.__schedule_next()

        value_next.set(state)
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

        async def _hatch():
            # Wait for the egg, but don't touch it! Unless you want spacetime
            # to fold into a singularity...
            interdimensional_egg: StateValue[_S] = await feathery_wormhole

            try:
                chick: _S = await self._producer.__anext__()
            except (StopAsyncIteration, asyncio.CancelledError):
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

        if hasattr(self, '_value_next'):
            self._value, self._value_next = self._value_next, egg
        else:
            self._value = self._value_next = egg

        feathery_wormhole.set_result(egg)
        return egg

    @abc.abstractmethod
    def _on_set(self, _: _S): ...
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

        return StateVarTuple(*(producer(i) for i in range(n)))

    def _on_set(self, _: _S):
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
        *producers: StateVarBase[_S] | AsyncIterable[_S],
    ) -> None:
        if not producers:
            raise TypeError('StateVarTuple() must have at least one argument.')

        items = []
        for i, producer in producers:
            if isinstance(producer, StateVarBase):
                items.append(producer)
            else:
                items.append(StateVar(producer))

        self._statevars = tuple(items)

        super().__init__(self._get_producer())

    def __await__(self) -> Generator[Any, None, tuple[_S, ...]]:
        return self._gather().__await__()

    @overload
    def __getitem__(self, i: int) -> StateVar[_S]: ...

    @overload
    def __getitem__(self, s: slice) -> StateVarTuple[_S]: ...

    def __getitem__(self, i: int | slice) -> StateVar[_S] | StateVarTuple[_S]:
        return self._statevars[i]

    def __len__(self) -> int:
        return len(self._statevars)

    @property
    def readonly(self) -> bool:
        return True

    def set(self, state: _S) -> NoReturn:
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

    def _on_set(self, _: _S):
        # TODO all_set
        self._set_predicate(set=True)

    def _on_stop(self):
        # TODO all_stopped
        self._set_predicate(done=True, error=False, cancel=False, stop=True)

    def _on_error(self, exc: BaseException):
        self._set_predicate(done=True, error=True, cancel=False, stop=False)

    def _on_cancel(self):
        # TODO all_cancelled
        self._set_predicate(done=True, error=False, cancel=True, stop=False)
