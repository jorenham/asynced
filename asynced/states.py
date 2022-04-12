from __future__ import annotations

__all__ = ('StateVar', 'StateVarTuple')

import abc
import asyncio
import collections
import inspect
import itertools
import sys
from typing import (
    AsyncIterable,
    AsyncIterator,
    Awaitable,
    Callable,
    ClassVar,
    Collection,
    Final,
    Generic,
    Hashable,
    Iterable,
    Iterator,
    Literal,
    NoReturn,
    overload,
    Sequence,
    TypeVar,
    Union,
)
from typing_extensions import TypeAlias

from ._states import SimpleStateBase, SimpleStateValue
from ._typing import ItemCollection, Maybe, Nothing, NothingType
from .asyncio_utils import race
from .exceptions import StateError, StopAnyIteration

_T = TypeVar('_T')
_K = TypeVar('_K')

_S = TypeVar('_S', bound=Hashable)
_S_co = TypeVar('_S_co', bound=Hashable, covariant=True)

_SS = TypeVar('_SS', bound=Collection[Hashable])

_RS = TypeVar('_RS', bound=Hashable)


AwaitablePredicate: TypeAlias = SimpleStateValue[bool]

_Counter: TypeAlias = Callable[[], int]
_DoneStatus: TypeAlias = Literal['stop', 'error', 'cancel']


def __intern_predicate_keys():
    for key in ('done', 'set', 'stop', 'error', 'cancel'):
        sys.intern(key)


__intern_predicate_keys()


class StateVarBase(AsyncIterator[_S], SimpleStateBase[_S], Generic[_S]):
    __slots__ = (
        '_predicates',

        '_future',
        '_waiters',
        '__waiter_counter',
    )

    _predicates: dict[str, AwaitablePredicate]

    _future: asyncio.Future[_S]
    _waiters: dict[int, asyncio.Future[_S]]
    _predicates: dict[str, AwaitablePredicate]

    def __init__(self) -> None:
        super().__init__()

        self._predicates = collections.defaultdict(SimpleStateValue)

        self._future = asyncio.get_running_loop().create_future()
        self._waiters = {}
        self.__waiter_counter = itertools.count(0).__next__

    def __aiter__(self: _S) -> AsyncIterator[_S]:
        async def _aiter():
            present = self._future

            while not self._is_done:
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
        self._check_next()

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
        self._check_next()

        if not (skip := self._equals(state)):
            self._set(state)

        return not skip

    @property
    def _is_done(self) -> bool:
        future = self._future
        if not future.done():
            return False

        if future.cancelled() or future.exception() is not None:
            return True

        return 'done' in self._predicates and bool(self._predicates['done'])

    def _map(
        self,
        function: Callable[[_S], _RS] | Callable[[_S], Awaitable[_RS]],
    ) -> AsyncIterator[_RS]:
        async def producer() -> AsyncIterator[_RS]:
            is_async = None
            if asyncio.iscoroutinefunction(function):
                is_async = True

            async for state in self:
                res = function(state)
                if is_async is None:
                    is_async = inspect.isawaitable(res)

                yield (await res) if is_async else res

        return producer()

    def _set(self, state: _S, always: bool = False,) -> None:
        if not always and self._equals(state):
            return

        self.__get_fresh_future().set_result(state)
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

        self.__get_fresh_future().set_exception(exc)

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

    def _equals(self, state: _S) -> bool:
        """Returns True if set and the argument is equal to the current state"""
        future = self._future
        if not future.done():
            return False

        # raises exception if thrown or cancelled
        state_current = future.result()
        if state is state_current:
            return True

        if state == state_current:
            return True

        return False

    def _set_predicate(self, **kwargs: bool) -> None:
        for key, value in kwargs.items():
            predicate = self._predicates[key]
            if not predicate._is_done:
                predicate.set(value)

    def _on_set(self, value: Maybe[_S] = Nothing) -> None:
        self._set_predicate(set=value is not Nothing)

    def _on_done(self, status: _DoneStatus):
        self._set_predicate(
            done=True,
            set=False,
            **{k: k == status for k in ('stop', 'error', 'cancel')}
        )

    def _on_stop(self) -> None:
        self._on_done('stop')

    def _on_error(self, exc: BaseException) -> None:
        if isinstance(exc, StopAnyIteration):
            self._on_stop()
        elif isinstance(exc, asyncio.CancelledError):
            self._on_cancel()
        else:
            self._on_done('error')

    def _on_cancel(self) -> None:
        self._on_done('cancel')

    def _check(self) -> None | NoReturn:
        future = self.future
        if future.done():
            # raises after set_exception() or cancel()
            future.result()

    def _check_next(self) -> None | NoReturn:
        self._check()
        if self._is_done:
            raise StopAsyncIteration

    def __get_fresh_future(self):
        future = self._future
        if future.done():
            future = self._future = future.get_loop().create_future()
        return future


_SV_co = TypeVar('_SV_co', bound=StateVarBase, covariant=True)


class StateCollectionBase(
    StateVarBase[_SS],
    Generic[_K, _S, _SS]
):
    __slots__ = (
        '_predicates_any',

        '_items_set',
        '_items_done',
    )

    _items_set: set[_K]
    _items_done: dict[_K, _DoneStatus]

    _predicates_any: dict[str, AwaitablePredicate]

    def __init__(self):
        super().__init__()

        self._predicates_any = collections.defaultdict(SimpleStateValue)

        self._items_set = set()
        self._items_done = {}
    
    @abc.abstractmethod
    def _get_collection(self) -> ItemCollection[_K, StateVarBase[_S]]: ...
    @abc.abstractmethod
    def _get_keys(self) -> Iterable[_K]: ...

    def __iter__(self) -> Iterator[StateVarBase[_S]]:
        return iter(self._get_collection())

    def __len__(self) -> int:
        return len(self._get_collection())

    def __getitem__(self, key: _K) -> StateVarBase[_S]:
        return self._get_collection()[key]
        
    def __setitem__(self, key: _K, value: _S) -> None:
        self._get_collection()[key].set(value)
        
    @property
    def readonly(self) -> bool:
        return any(s.readonly for s in self)
    
    @property
    def any_set(self) -> AwaitablePredicate:
        return self._predicates_any['set']

    @property
    def any_done(self) -> AwaitablePredicate:
        return self._predicates_any['done']

    @property
    def any_stopped(self) -> AwaitablePredicate:
        return self._predicates_any['stop']

    @property
    def any_error(self) -> AwaitablePredicate:
        return self._predicates_any['error']

    @property
    def any_cancelled(self) -> AwaitablePredicate:
        return self._predicates_any['cancel']

    def set(self, state: tuple[_S, ...]) -> NoReturn:
        raise StateError(f'{type(self).__name__!r} cannot be set directly')

    def _get_futures(self) -> Iterable[asyncio.Future[_S]]:
        for s in self:
            yield s.future

    def _on_item_set(self, item: _K, value: Maybe[_S] = Nothing) -> None:
        items_set = self._items_set
        all_set = len(items_set | {item}) == len(self)

        if all_set:
            state = self.get(default=value)
            self._set(state)  # this calls _on_set()

        if item in items_set:
            return

        if not items_set:
            self._predicates_any['set'].set(value is not Nothing)

        items_set.add(item)

    def _on_item_done(self, item: _K, status: _DoneStatus) -> None:
        items_done = self._items_done
        if item in items_done:
            return

        if not items_done:
            self._predicates_any['done'].set(True)

            for key in ('stop', 'error', 'cancel'):
                if status == key:
                    self._predicates_any[key].set(True)
                else:
                    self._predicates[key].set(False)

        items_done[item] = status

        if len(items_done) == len(self):
            self._on_done(status)

    def _on_item_stop(self, item: _K) -> None:
        self._on_item_done(item, 'stop')

    def _on_item_error(self, item: _K, exc: BaseException) -> None:
        if isinstance(exc, (StopIteration, StopAsyncIteration, GeneratorExit)):
            self._on_item_stop(item)
            return
        if isinstance(exc, asyncio.CancelledError):
            self._on_item_cancel(item)
            return

        self._raise(exc)
        self._on_item_done(item, 'error')

    def _on_item_cancel(self, item: _K) -> None:
        self._cancel()
        self._on_item_done(item, 'cancel')


class StateVar(StateVarBase[_S], Generic[_S]):
    __slots__ = (
        '_name',
        '_i',

        '_producer',
        '_consumer',

        '_parent_collections',
    )

    _task_counter: ClassVar[_Counter] = itertools.count(0).__next__

    _name: Final[str]
    _i: int

    _producer: Maybe[AsyncIterator[_S]]
    _consumer: Final[Maybe[asyncio.Task[NoReturn]]]

    # Internal: for change notification to e.g. StateVarTuple
    _parent_collections: list[tuple[_K, StateCollectionBase[_K, _S]]]

    def __init__(
        self,
        producer: Maybe[StateVar[_S] | AsyncIterable[_S]] = Nothing,
        *,
        name: str | None = None,
        start: int | None = None,
    ) -> None:
        super().__init__()

        if name is None:
            self._name = f'{type(self).__name__}_{self._task_counter()}'
        else:
            self._name = name

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

        if start is None and isinstance(producer, type(self)):
            if other_at := producer.at(None):
                start = other_at - 1  # -1 because we have no value yet

        self._i = 0 if start is None else start

        self._parent_collections = []

    @property
    def name(self) -> str:
        return self._name

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

    def map(
        self,
        function: Callable[[_S], _RS] | Callable[[_S], Awaitable[_RS]],
    ) -> StateVar[_RS]:
        # TODO docstring
        name = f'{function.__name__}({self._name})'
        return type(self)(self._map(function), name=name)

    def split(self: StateVar[Sequence[_RS, ...]]) -> StateVarTuple[_RS]:
        # TODO docstring
        n = len(self.get())

        async def producer(i: int):
            async for state in self:
                yield state[i]

        return StateVarTuple(map(producer, range(n)))

    @property
    def _is_done(self) -> bool:
        consumer = self._consumer
        if consumer is not Nothing and consumer.done():
            return True

        return super()._is_done
    
    def _get_task_name(self) -> str:
        return self._name
    
    def _check(self) -> None | NoReturn:
        super()._check()

        consumer = self._consumer
        if consumer is not Nothing and consumer.done():
            if consumer.exception() is not None:
                consumer.result()  # raises exception
            elif consumer.cancelled() and not self._is_set:
                consumer.result()  # raises asyncio.CancelledError

    def _check_next(self) -> None | NoReturn:
        consumer = self._consumer
        if consumer is not Nothing and consumer.done():
            consumer.result()
            raise StopAsyncIteration

        super()._check_next()

    async def _consume(self):
        try:
            async for state in self._producer:
                self._set(state)
        except (SystemExit, KeyboardInterrupt) as exc:
            self._raise(exc)
            raise
        except StopAnyIteration:
            self._stop()
        except asyncio.CancelledError:
            self._cancel()
        except BaseException as exc:
            self._raise(exc)
        else:
            self._stop()

    def _on_set(self, state: Maybe[_S] = Nothing) -> None:
        self._i += 1
        
        super()._on_set(state)

        for j, parent in self._parent_collections:
            # noinspection PyProtectedMember
            parent._on_item_set(j, state)

    def _on_stop(self) -> None:
        super()._on_stop()

        for j, parent in self._parent_collections:
            # noinspection PyProtectedMember
            parent._on_item_stop(j)

    def _on_error(self, exc: BaseException) -> None:
        super()._on_error(exc)

        for j, parent in self._parent_collections:
            # noinspection PyProtectedMember
            parent._on_item_error(j, exc)

    def _on_cancel(self) -> None:
        super()._on_cancel()

        for j, parent in self._parent_collections:
            # noinspection PyProtectedMember
            parent._on_item_cancel(j)


class StateVarTuple(StateCollectionBase[int, tuple[_S, ...], _S], Generic[_S]):
    __slots__ = ('_statevars', )

    _statevars: tuple[StateVar[_S], ...]

    def __init__(
        self,
        producers: int | Iterable[StateVar[_S] | AsyncIterable[_S]],
    ) -> None:
        super().__init__()

        if isinstance(producers, int):
            statevars = [StateVar() for _ in range(producers)]
        else:
            statevars = []
            for i, producer in enumerate(producers):
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

                # noinspection PyProtectedMember
                statevar._parent_collections.append((i, self))

                statevars.append(statevar)

        if not statevars:
            raise TypeError('StateVarTuple() requires at least one statevar.')

        self._statevars = tuple(statevars)

    def __hash__(self) -> int:
        return hash(self._statevars)

    def __contains__(self, item: object) -> bool:
        if isinstance(item, StateVarBase):
            return item in self._statevars

        default = object()
        return item in tuple(s for s in self.get(default) if s is not default)

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

    @overload
    def __getitem__(self, __k: int) -> StateVar[_S]: ...
    @overload
    def __getitem__(self, __ks: tuple[int]) -> StateVarTuple[_S]: ...
    @overload
    def __getitem__(self, __ks: slice) -> StateVarTuple[_S]: ...

    def __getitem__(
        self,
        index: int | tuple[int] | slice
    ) -> StateVarBase[_S]:
        if isinstance(index, int):
            return super().__getitem__(index)
        elif isinstance(index, tuple):
            return type(self)(self[i] for i in index)
        elif isinstance(index, slice):
            return type(self)(self[i] for i in range(*index.indices(len(self))))
        else:
            raise TypeError(
                f'{type(self).__name__} indices must be integers or slices, '
                f'not {type(index).__name__}'
            )
        
    @property
    def readonly(self) -> bool:
        """.set() cannot be used, but unless the statevar items are readable,
        they can be set with e.g. self[0] = ...
        """
        return True

    @overload
    def at(self, default: _T) -> tuple[int | _T, ...]: ...
    @overload
    def at(self, default: NothingType = ...) -> tuple[int, ...]: ...

    def at(self, default: Maybe[_T] = Nothing) -> tuple[int | _T, ...]:
        """Get current iteration counts of each statevar, starting at 0."""
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

    def map(
        self,
        function: Union[
            Callable[[tuple[_S, ...]], _RS],
            Callable[[tuple[_S, ...]], Awaitable[_RS]]
        ],
    ) -> StateVar[_RS]:
        return StateVar(self._map(function))

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

    def _get_collection(self) -> tuple[StateVar[_S], ...]:
        return self._statevars

    def _get_keys(self) -> Iterable[int]:
        return range(len(self._statevars))
