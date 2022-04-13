from __future__ import annotations

__all__ = ('StateBase', 'StateValue', 'State', 'StateCollection')

import abc
import asyncio
import collections
import inspect
import itertools
import sys
from typing import (
    cast,
    overload,
    Any,
    AsyncIterator,
    Awaitable,
    Callable,
    ClassVar,
    Collection,
    Coroutine,
    Final,
    Generator,
    Generic,
    Hashable,
    Iterable,
    Iterator,
    Literal,
    NoReturn,
    TypeVar,
    Union,
)

from typing_extensions import ParamSpec, TypeAlias

from ._typing import Comparable, ItemCollection, Maybe, Nothing, NothingType
from .exceptions import StopAnyIteration, StateError

_T = TypeVar('_T')
_KT = TypeVar('_KT')
_VT = TypeVar('_VT')
_RT = TypeVar('_RT')

_S = TypeVar('_S', bound=Hashable)
_RS = TypeVar('_RS', bound=Hashable)
_SS = TypeVar('_SS', bound=Collection)

_P = ParamSpec('_P')

_FutureOrCoro: TypeAlias = Union[asyncio.Future[_T], Coroutine[Any, Any, _T]]
_MaybeAsync: TypeAlias = Union[Callable[_P, _T], Callable[_P, Awaitable[_T]]]
_DoneStatus: TypeAlias = Literal['stop', 'error', 'cancel']

_DONE_STATUS_KEYS: Final[tuple[_DoneStatus, ...]] = 'stop', 'error', 'cancel'


class StateBase(Generic[_S]):
    __slots__ = ()
    __match_args__ = ('_raw_value', )

    def __del__(self):
        try:
            self.future.cancel()
        except (AttributeError, RuntimeError):
            pass

    def __await__(self) -> Generator[Any, None, _S]:
        return self.future.__await__()

    def __repr__(self) -> str:
        return f'<{type(self).__name__}{self._format()} at {id(self):#x}>'

    def __str__(self) -> str:
        return f'<{type(self).__name__}{self._format()}>'

    @property
    @abc.abstractmethod
    def future(self) -> asyncio.Future[_S]:
        ...

    @property
    def readonly(self) -> bool:
        """Returns true if an asyncio.Task will set the result, otherwise, the
        asyncio.Future can be set manually.
        """
        return isinstance(self.future, asyncio.Task)

    @property
    def _is_done(self) -> bool:
        return self.future.done()

    @property
    def _is_set(self) -> bool:
        future = self.future
        if not future.done():
            return False
        if future.cancelled():
            return False
        return future.exception() is None

    @property
    def _is_raised(self) -> bool:
        future = self.future
        if not future.done():
            return False
        if future.cancelled():
            return False
        if (exc := future.exception()) is None:
            return False

        return not isinstance(exc, asyncio.CancelledError)

    @property
    def _is_cancelled(self) -> bool:
        future = self.future
        if not future.done():
            return False
        if future.cancelled():
            return True
        return isinstance(future.exception(), asyncio.CancelledError)

    @property
    def _raw_value(self) -> _S | BaseException | None:
        """For pattern matching."""
        fut = self.future
        if not fut.done():
            return None
        try:
            return fut.result()
        except BaseException as exc:
            return exc
    
    def _set(self, value: _S) -> None:
        self.__as_unset_future().set_result(value)
        self._on_set(value)

    def _raise(self, exc: BaseException) -> None:
        if isinstance(exc, asyncio.CancelledError):
            self._cancel()
            return

        self.__as_unset_future().set_exception(exc)
        self._on_error(exc)

    def _cancel(self) -> None:
        self.future.cancel()
        self._on_cancel()

    def _on_set(self, value: Maybe[Hashable] = Nothing) -> None:
        ...

    def _on_error(self, exc: BaseException) -> None:
        ...

    def _on_cancel(self) -> None:
        ...

    def __as_unset_future(self):
        future = self.future
        if self.readonly:
            raise StateError(f'{self!r} is readonly')

        if future.done():
            current = future.result()
            raise StateError(f'{self!r} is already set: {current!r}')

        return future

    def _format(self) -> str:
        fut = self.future

        if fut.done():
            return f'({self._raw_value!r})'

        return ''


class StateValue(StateBase[_S], Generic[_S]):
    __slots__ = ('_future', )

    _task_counter: ClassVar[Callable[[], int]] = itertools.count(1).__next__

    _future: asyncio.Future[_S]

    def __init__(
        self,
        coro: Maybe[_FutureOrCoro[_S]] = Nothing,
        *,
        name: str | None = None
    ) -> None:
        loop = asyncio.get_running_loop()

        if coro is Nothing:
            self._future = loop.create_future()
        elif isinstance(coro, asyncio.Future):
            self._future = coro
        elif asyncio.iscoroutine(coro):
            self._future = loop.create_task(coro, name=name)
        else:
            raise TypeError(f'a future or coroutine was expected, got {coro!r}')

        if isinstance(self._future, asyncio.Task):
            self._future.add_done_callback(self._on_task_done)

    def __bool__(self) -> bool:
        return bool(self._raw_value)

    @property
    def future(self) -> asyncio.Future[_S]:
        return self._future

    def set(self, state: _S):
        self._set(state)

    def clear(self) -> None:
        future = self._future
        if future.done():
            self._future = future.get_loop().create_future()

    def _on_task_done(self, task: asyncio.Task[_S]):
        assert task.done()

        if task.cancelled():
            self._on_cancel()
        elif (exc := task.exception()) is not None:
            self._on_error(exc)

            if isinstance(exc, (SystemExit, KeyboardInterrupt)):
                raise
        else:
            self._on_set(task.result())


_AwaitablePredicate: TypeAlias = StateValue[bool]


def __intern_predicate_keys():
    for key in ('done', 'set') + _DONE_STATUS_KEYS:
        sys.intern(key)


__intern_predicate_keys()


class State(AsyncIterator[_S], StateBase[_S], Generic[_S]):
    __slots__ = (
        '_key',

        '_predicates',

        '_future',
        '_waiters',
        '__waiter_counter',

        '_collections',
    )

    _key: Callable[[_S], Comparable]

    _predicates: dict[str, _AwaitablePredicate]

    _future: asyncio.Future[_S]
    _waiters: dict[int, asyncio.Future[_S]]
    _predicates: dict[str, _AwaitablePredicate]

    # Internal: for change notification to e.g. StateVarTuple
    _collections: list[tuple[Any, StateCollection[Any, _S, Any]]]

    def __init__(
        self,
        *,
        key: Callable[[_S], Comparable] = lambda s: s
    ) -> None:
        super().__init__()

        self._key = key

        self._predicates = collections.defaultdict(StateValue)

        self._future = asyncio.get_running_loop().create_future()
        self._waiters = {}
        self.__waiter_counter = itertools.count(0).__next__

        # TODO use weakref
        self._collections = []

    def __aiter__(self) -> AsyncIterator[_S]:
        async def _aiter():
            present = self._future
            loop = present.get_loop()

            while not self._is_done:
                waiter_id = self.__waiter_counter()
                future: asyncio.Future[_S] = loop.create_future()
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
    def is_set(self) -> _AwaitablePredicate:
        return self._predicates['set']

    @property
    def is_done(self) -> _AwaitablePredicate:
        return self._predicates['done']

    @property
    def is_stopped(self) -> _AwaitablePredicate:
        return self._predicates['stop']
    
    @property
    def is_error(self) -> _AwaitablePredicate:
        return self._predicates['error']
    
    @property
    def is_cancelled(self) -> _AwaitablePredicate:
        return self._predicates['cancel']

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

    @overload
    def _map(self, __f: Callable[[_S], Awaitable[_RS]]) -> AsyncIterator[_RS]:
        ...

    @overload
    def _map(self, __f: Callable[[_S], _RS]) -> AsyncIterator[_RS]:
        ...

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

                if is_async:
                    yield await cast(Awaitable[_RS], res)
                else:
                    yield cast(_RS, res)

        return producer()
    
    def _get(self, default: Maybe[_T] = Nothing) -> _S | _T:
        future = self.future
        if future.done():
            try:
                return future.result()
            except (StopAsyncIteration, asyncio.CancelledError):
                if default is not Nothing:
                    return default
                raise

        if default is not Nothing:
            return default

        raise LookupError(repr(self))
    
    def _set(self, value: _S, always: bool = False) -> None:
        if not always and self._equals(value):
            return

        self.__get_fresh_future().set_result(value)
        self._on_set(value)

        for waiter in self._waiters.values():
            if not waiter.done():
                waiter.set_result(value)

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

        try:
            self._on_cancel()
        except RuntimeError:
            # can occur when the event got closed
            pass

        for waiter in self._waiters.values():
            if not waiter.done():
                waiter.cancel()

    def _equals(self, state: _S) -> bool:
        """Returns True if set and the argument is equal to the current state"""
        future = self._future
        if not future.done():
            return False

        # raises exception if thrown or cancelled
        key = self._key(state)
        key_current = self._key(future.result())

        return key is key_current or key == key_current

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
            **{k: k == status for k in _DONE_STATUS_KEYS}
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


class StateCollection(State[_SS], Generic[_KT, _S, _SS]):
    __slots__ = (
        '_predicates_any',

        '_items_set',
        '_items_done',
    )

    _items_set: set[_KT]
    _items_done: dict[_KT, _DoneStatus]

    _predicates_any: dict[str, _AwaitablePredicate]

    def __init__(self):
        # TODO key
        super().__init__()

        self._predicates_any = collections.defaultdict(StateValue)

        self._items_set = set()
        self._items_done = {}

    @abc.abstractmethod
    def _get_states(self) -> ItemCollection[_KT, State[_S]]: ...

    @overload
    @abc.abstractmethod
    def _get_data(self, default: NothingType = ...) -> _SS: ...
    @overload
    @abc.abstractmethod
    def _get_data(self, default: _S = ...) -> _SS: ...
    @abc.abstractmethod
    def _get_data(self, default: Maybe[_S] = Nothing) -> _SS: ...

    def __iter__(self) -> Iterator[State[_S]]:
        return iter(self._get_states())

    def __len__(self) -> int:
        return len(self._get_states())

    def __getitem__(self, key: _KT) -> State[_S]:
        return self._get_states()[key]

    def __setitem__(self, key: _KT, value: _S) -> None:
        self._get_states()[key].set(value)

    @property
    def readonly(self) -> bool:
        return any(s.readonly for s in self)

    @property
    def any_set(self) -> _AwaitablePredicate:
        return self._predicates_any['set']

    @property
    def any_done(self) -> _AwaitablePredicate:
        return self._predicates_any['done']

    @property
    def any_stopped(self) -> _AwaitablePredicate:
        return self._predicates_any['stop']

    @property
    def any_error(self) -> _AwaitablePredicate:
        return self._predicates_any['error']

    @property
    def any_cancelled(self) -> _AwaitablePredicate:
        return self._predicates_any['cancel']

    @overload
    def get(self, key: _KT, default: NothingType = ...) -> _S: ...
    @overload
    def get(self, key: _KT, default: _T = ...) -> _S | _T: ...
    @abc.abstractmethod
    def get(self, key: _KT, default: Maybe[_T] = Nothing) -> _S | _T: ...

    def set(self, state: tuple[_S, ...]) -> NoReturn:
        raise StateError(f'{type(self).__name__!r} cannot be set directly')

    def _get_futures(self) -> Iterable[asyncio.Future[_S]]:
        for s in self:
            yield s.future

    def _on_item_set(self, item: _KT, value: Maybe[_S] = Nothing) -> None:
        items_set = self._items_set
        all_set = len(items_set | {item}) == len(self)

        if all_set and not self._is_raised:
            state = cast(_SS, self._get_data(default=value))
            self._set(state)  # this calls _on_set()

        if item in items_set:
            return

        if not items_set:
            self._predicates_any['set'].set(value is not Nothing)

        items_set.add(item)

    def _on_item_del(self, item: _KT) -> None:
        if item in self._items_set:
            self._items_set.remove(item)

            if not self._items_set:
                self._predicates_any['set'].clear()
                self._predicates['set'].clear()

        elif len(self._items_set) > 1 and len(self._items_set) == len(self):
            state = cast(_SS, self._get_data())
            self._set(state)

        if item in self._items_done:
            status = self._items_done.pop(item)

            empty = self._items_done

            for key in _DONE_STATUS_KEYS:
                if key in self._predicates:
                    predicate = self._predicates[key]
                    future = predicate.future

                    if empty:
                        predicate.clear()
                        self._predicates_any[key].clear()

                    elif (
                        key != status
                        and future.done()
                        and not future.result()
                        and all(key == k for k in self._items_done.values())
                    ):
                        predicate.clear()
                        predicate.set(True)

                elif empty and key in self._predicates_any:
                    self._predicates_any[key].clear()

    def _on_item_done(self, item: _KT, status: _DoneStatus) -> None:
        items_done = self._items_done
        if item in items_done:
            return

        if not items_done:
            p_all = self._predicates
            p_any = self._predicates_any

            self._predicates_any['done'].set(True)

            for key in _DONE_STATUS_KEYS:
                if status == key:
                    p_any[key].set(True)
                else:
                    if key not in p_all or not p_all[key]._is_done:
                        p_all[key].set(False)

        items_done[item] = status

        if len(items_done) == len(self):
            self._on_done(status)

    def _on_item_stop(self, item: _KT) -> None:
        self._stop()
        self._on_item_done(item, 'stop')

    def _on_item_error(self, item: _KT, exc: BaseException) -> None:
        if isinstance(exc, (StopIteration, StopAsyncIteration, GeneratorExit)):
            self._on_item_stop(item)
            return
        if isinstance(exc, asyncio.CancelledError):
            self._on_item_cancel(item)
            return

        self._raise(exc)
        self._on_item_done(item, 'error')

    def _on_item_cancel(self, item: _KT) -> None:
        self._cancel()
        self._on_item_done(item, 'cancel')
