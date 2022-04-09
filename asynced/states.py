from __future__ import annotations

__all__ = ('StateVar', 'StateVarTuple')

import abc
import asyncio
import collections
import inspect
import itertools
import sys
from typing import (
    Any,
    AsyncIterable,
    AsyncIterator,
    Awaitable,
    Callable,
    ClassVar,
    Final, Generator,
    Generic,
    Hashable,
    Iterable,
    NoReturn,
    overload,
    Sequence,
    TypeVar,
)
from typing_extensions import Self, TypeAlias

from ._states import SimpleStateBase, SimpleStateValue
from ._typing import Maybe, Nothing, NothingType
from .asyncio_utils import race
from .exceptions import StateError, StopAnyIteration

_T = TypeVar('_T')
_K = TypeVar('_K')

_S = TypeVar('_S', bound=Hashable)
_RS = TypeVar('_RS', bound=Hashable)


AwaitablePredicate: TypeAlias = SimpleStateValue[bool]
_Counter: TypeAlias = Callable[[], int]


def __intern_predicate_keys():
    for key in ('done', 'set', 'stop', 'error', 'cancel'):
        sys.intern(key)


__intern_predicate_keys()


class StateBase(SimpleStateBase[_S], Generic[_S]):
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
            if not predicate._is_done:
                predicate.set(value)

    @abc.abstractmethod
    def _on_set(self, value: _S) -> None: ...

    @abc.abstractmethod
    def _on_stop(self) -> None: ...

    @abc.abstractmethod
    def _on_error(self, exc: BaseException) -> None: ...

    @abc.abstractmethod
    def _on_cancel(self) -> None: ...


class StateCollectionBase(SimpleStateBase[_S], Generic[_K, _S]):
    __slots__ = ()

    # TODO: predicates with .all() and .any()
    #  (e.g. subclass of StateVar[tuple[int, int]])

    @abc.abstractmethod
    def _on_item_set(self, i: _K, value: _S) -> None: ...

    @abc.abstractmethod
    def _on_item_stop(self, i: _K) -> None: ...

    @abc.abstractmethod
    def _on_item_error(self, i: _K, exc: BaseException) -> None: ...

    @abc.abstractmethod
    def _on_item_cancel(self, i: _K) -> None: ...


class StateVarBase(AsyncIterator[_S], StateBase[_S], Generic[_S]):
    __slots__ = (
        '_name',

        '_producer',
        '_consumer',

        '_future',
        '_waiters',
        '_predicates',

        '__waiter_counter',
    )

    _task_counter: ClassVar[_Counter] = itertools.count(0).__next__
    
    _name: Final[str]

    _producer: Maybe[AsyncIterator[_S]]
    _consumer: Final[Maybe[asyncio.Task[NoReturn]]]

    _future: asyncio.Future[_S]
    _waiters: dict[int, asyncio.Future[_S]]
    _predicates: dict[str, AwaitablePredicate]

    def __init__(
        self,
        producer: Maybe[StateVar[_S] | AsyncIterable[_S]] = Nothing,
        *,
        name: str | None = None,
    ) -> None:
        if name is None:
            self._name = f'{type(self).__name__}_{self._task_counter()}'
        else:
            self._name = name

        self._future = asyncio.get_running_loop().create_future()
        self._waiters = {}
        self._predicates = collections.defaultdict(SimpleStateValue)

        if producer is not Nothing:
            producer = producer
            consumer = asyncio.create_task(
                self._consume(),
                name=f'{self._name}.consumer'
            )
        else:
            consumer = Nothing

        self._producer = producer
        self._consumer = consumer
        
        self.__waiter_counter = itertools.count(0).__next__

    def __await__(self) -> Generator[Any, None, _S]:
        # future = self._future
        # if future.done():
        return self.future.__await__()

        # return self.__anext__().__await__()

    def __aiter__(self: _S) -> AsyncIterator[_S]:
        async def _aiter():
            present = self._future

            while self._consumer is Nothing or not self._consumer.done():
                waiter_id = self.__waiter_counter()
                future = self._future.get_loop().create_future()
                self._waiters[waiter_id] = future

                if present is not None and present.done():
                    try:
                        yield present.result()
                    except StopAsyncIteration:
                        del self._waiters[waiter_id]
                        break

                present = None

                try:
                    yield await future
                except StopAsyncIteration:
                    break
                finally:
                    del self._waiters[waiter_id]

        return _aiter()

    async def __anext__(self) -> _S:
        self._raise_if_done()

        waiter_id = self.__waiter_counter()
        future = self._future.get_loop().create_future()
        self._waiters[waiter_id] = future

        try:
            return await future
        except asyncio.CancelledError as exc:
            raise StopAsyncIteration from exc
        finally:
            del self._waiters[waiter_id]

    @property
    def future(self) -> asyncio.Future[_S]:
        return self._future

    @property
    def name(self) -> str:
        return self._name

    @property
    def _init_kwargs(self) -> dict[str, Any]:
        return {}

    def get(self, default: Maybe[_T] = Nothing) -> _S | _T:
        """Return the current state value.

        If not set, the method will return the default value of the `default`
        argument of the method, if provided; or raise a LookupError.
        """
        fut = self.future
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

    def set(self, state: _S) -> bool:
        """Set the new state. If the state is equal to the current state,
        false is returned. Otherwise, the state is set, the waiters will
        be notified, and true is returned.

        Raises StateError if readonly.
        """
        self._raise_if_done()

        future = self.future
        if future.done():
            # raises exception if thrown or cancelled
            state_current = future.result()

            if state is state_current or state == state_current:
                return False

        self._set(state)

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

        return type(self)(
            producer(),
            name=f'{function.__name__}({self._name})',
            **self._init_kwargs
        )

    async def _consume(self):
        try:
            async for state in self._producer:
                self._set(state)
        except (SystemExit, KeyboardInterrupt) as exc:
            raise
        except GeneratorExit:
            pass
        except StopAnyIteration:
            self._stop()
        except asyncio.CancelledError:
            self._cancel()
        except BaseException as exc:
            self._raise(exc)
        else:
            self._stop()

    def _raise_if_done(self) -> bool:
        consumer = self._consumer
        if consumer is Nothing:
            return False

        if consumer.done():
            consumer.result()
            raise StopAsyncIteration

        return True

    def _set(self, state: _S) -> None:
        future = self._future
        if future.done():
            # raises exception if thrown or cancelled
            state_current = future.result()

            if state is state_current or state == state_current:
                return

            future = self._future = future.get_loop().create_future()

        future.set_result(state)
        self._on_set(state)

        for waiter in self._waiters.values():
            if not waiter.done():
                waiter.set_result(state)

    def _stop(self) -> None:
        future = self._future
        if not future.done():
            future.set_exception(StopAsyncIteration)

        self._on_stop()

        for waiter in self._waiters.values():
            if not waiter.done():
                waiter.set_exception(StopAsyncIteration)

    def _raise(self, exc: BaseException) -> None:
        if isinstance(exc, asyncio.CancelledError):
            self._cancel()
            return

        future = self.__refresh_future().set_exception(exc)

        self._on_error(exc)

        for waiter in self._waiters.values():
            if not waiter.done():
                waiter.set_exception(exc)

    def _cancel(self) -> None:
        self._future.cancel()

        self._on_cancel()

        for waiter in self._waiters.values():
            if not waiter.done():
                waiter.cancel()

    def _on_set(self, value: _S) -> None:
        self._set_predicate(set=True)

    def _on_stop(self) -> None:
        self._set_predicate(
            done=True,
            stop=True,
            set=False,
            error=False,
            cancel=False
        )
    
    def _on_error(self, exc: BaseException) -> None:
        if isinstance(exc, StopAnyIteration):
            self._on_stop()
            return

        self._set_predicate(
            done=True,
            stop=False,
            set=False,
            error=True,
            cancel=False
        )
    
    @abc.abstractmethod
    def _on_cancel(self) -> None:
        self._set_predicate(
            done=True,
            stop=False,
            set=False,
            error=False,
            cancel=True,
        )

    def __refresh_future(self):
        future = self._future
        if future.done():
            future = self._future = future.get_loop().create_future()
        return future
    

class StateVar(StateVarBase[_S], Generic[_S]):
    __slots__ = ('_i', '_callbacks')

    _i: int

    # Internal: for change notification to e.g. StateVarTuple
    _callbacks: list[Callable[[Self], None]]

    def __init__(
        self,
        producer: Maybe[StateVar[_S] | AsyncIterable[_S]] = Nothing,
        *,
        name: str | None = None,
        start: int | None = None,
    ) -> None:
        super().__init__(producer, name=name)

        if start is None and isinstance(producer, type(self)):
            if other_at := producer.at(None):
                start = other_at - 1  # -1 because we have no value yet

        self._i = 0 if start is None else start

    def at(self, default: Maybe[_T] = Nothing) -> int | _T:
        """Get current iteration count, starting at 0.

        If not set, raises LookupError.
        """
        # TODO return an int-like statevar
        if hasattr(self, '_value'):
            fut = self._value.future()
            if fut.done() and not fut.cancelled():
                return self._i

        if default is not Nothing:
            return default

        raise LookupError(repr(self))

    def next_at(self) -> int:
        """Return the next iteration count, starting at 0."""
        return self.at(self._i - 1) + 1

    def split(self: StateVar[Sequence[_RS, ...]]) -> StateVarTuple[_RS]:
        # TODO docstring
        n = len(self.get())

        async def producer(i: int):
            async for state in self:
                yield state[i]

        return StateVarTuple(map(producer, range(n)), name=f'*{self.name}')

    @property
    def _init_kwargs(self) -> dict[str, Any]:
        if self._i > 0:
            return {'start': self._i}

    def _on_set(self, state: _S):
        self._i += 1
        self._set_predicate(set=True)

    def _on_stop(self):
        self._set_predicate(done=True, stop=True, error=False, cancel=False)

    def _on_error(self, exc: BaseException):
        self._set_predicate(done=True, stop=False, error=True, cancel=False)

    def _on_cancel(self):
        self._set_predicate(done=True, stop=False, error=False, cancel=True)


class StateVarTuple(
    Sequence[StateVar[_S]],
    StateCollectionBase[int, tuple[_S, ...]],
    Generic[_S],
):
    __slots__ = ('_statevars', )

    _statevars: tuple[StateVar[_S], ...]

    def __init__(
        self,
        producers: int | Iterable[StateVar[_S] | AsyncIterable[_S]],
        *,
        name: str | None = None,
    ) -> None:
        if isinstance(producers, int):
            statevars = [StateVar() for _ in range(producers)]
        else:
            statevars = []
            for producer in producers:
                if isinstance(producer, StateVar):
                    statevar = producer
                elif isinstance(producer, StateVarTuple):
                    raise TypeError(
                        f'{type(self).__name__!r} cannot contain itself'
                    )
                elif not isinstance(producer, AsyncIterable):
                    raise TypeError(
                        f'expected an iterable of StateVar\'s or async '
                        f'iterables, iterable contains '
                        f'{type(producer).__name__!r} instead'
                    )
                else:
                    statevar = StateVar(producer)

                statevars.append(statevar)

        if not statevars:
            raise TypeError('StateVarTuple() requires at least one statevar.')

        self._statevars = tuple(statevars)

        super().__init__(self._get_producer(), name=name)

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
        self._on_set()

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

    def at(self, default: Maybe[_T] = Nothing) -> tuple[int | _T, ...]:
        """
        Get current iteration counts of each statevar, starting at 0.

        If any
        """
        return tuple(s.at(default) for s in self._statevars)

    def next_at(self) -> tuple[int, ...]:
        """Return the next iteration counts, starting at 0."""
        return tuple(s.next_at() for s in self._statevars)

    @overload
    def get(self, default: NothingType = ...) -> tuple[_S, ...]: ...
    @overload
    def get(self, default: _T = ...) -> tuple[_S | _T, ...]: ...

    def get(self, default: Maybe[_T] = Nothing) -> tuple[_S | _T, ...]:
        return tuple(sv.get(default) for sv in self._statevars)

    def set(self, state: tuple[_S, ...]) -> NoReturn:
        raise StateError(f'{type(self).__name__!r} cannot be set directly')

    def starmap(
        self,
        function: Callable[..., _RS] | Callable[..., Awaitable[_RS]],
    ) -> StateVar[_RS]:
        return self.map(lambda args: function(*args))

    # PyCharm doesn't understand walrus precedence:
    # noinspection PyRedundantParentheses
    async def _get_producer(self) -> AsyncIterator[tuple[_S]]:
        try:
            yield (states := tuple(await asyncio.gather(*self._statevars)))

            async for i, state in race(*self._statevars):
                yield (states := states[:i] + (state, ) + states[i+1:])

        except StopAsyncIteration:
            pass

    def _update(self, future: asyncio.Future[_S]) -> None:
        super()._update(future)

    def _on_set(self):
        if self._predicates['n_set'].future().done():
            return

        self._set_predicate(set=True)

        if (n_set := self.__all_predicates('set')) is not None:
            self._set_predicate(n_set=n_set)

    def _on_stop(self):
        self._set_predicate(stop=True)

        if (n_stop := self.__all_predicates('stop')) is not None:
            self._set_predicate(n_error=False, n_cancel=False, n_stop=n_stop)
            self.__on_all_done()

    def _on_error(self, exc: BaseException):
        self._set_predicate(error=True)

        if (n_error := self.__all_predicates('error')) is not None:
            self._set_predicate(n_error=n_error, n_cancel=False, n_stop=False)
            self.__on_all_done()

    def _on_cancel(self):
        self._set_predicate(cancel=True)

        if (n_cancel := self.__all_predicates('cancel')) is not None:
            self._set_predicate(n_error=False, n_cancel=n_cancel, n_stop=False)
            self.__on_all_done()

    def __on_all_done(self):
        self._set_predicate(n_done=True)
        if not self._predicates['n_set'].future().done():
            self._set_predicate(n_set=False)

    def __all_predicates(self, key: str) -> bool | None:
        if len(self) == 1:
            future = self._predicates[key].future()
            return future.result() if future.done() else None

        is_all = True
        for statevar in self:
            # noinspection PyProtectedMember
            predicates = statevar._predicates
            if key not in predicates:
                return None

            predicate = predicates[key]
            future = predicate.future()
            if not future.done():
                return None

            is_all = is_all and future.result()

        return is_all
