from __future__ import annotations

__all__ = ('StateBase', 'State', 'StateCollection')

import abc
import asyncio
import collections
import itertools
from typing import (
    Any,
    AsyncIterable,
    AsyncIterator,
    Awaitable,
    Callable,
    cast,
    Collection,
    Coroutine,
    Final,
    Generator,
    Generic,
    Iterator,
    Literal,
    Mapping,
    NoReturn,
    overload,
    TypeVar,
    Union,
)

from typing_extensions import ParamSpec, Self, TypeAlias

from . import amap_iter
from ._typing import awaitable, Comparable, Maybe, Nothing, NothingType
from .exceptions import StateError, StopAnyIteration

_T = TypeVar('_T')
_KT = TypeVar('_KT')
_VT = TypeVar('_VT')
_RT = TypeVar('_RT')

_S = TypeVar('_S', bound=object)
_RS = TypeVar('_RS', bound=object)
_SS = TypeVar('_SS', bound=Collection)

_P = ParamSpec('_P')

_FutureOrCoro: TypeAlias = Union[asyncio.Future[_T], Coroutine[Any, Any, _T]]
_MaybeAsync: TypeAlias = Union[Callable[_P, _T], Callable[_P, Awaitable[_T]]]
_DoneStatus: TypeAlias = Literal['stop', 'error', 'cancel']

_DONE_STATUS_KEYS: Final[tuple[_DoneStatus, ...]] = 'stop', 'error', 'cancel'


_ST = TypeVar('_ST', bound='State')


class StateBase(Generic[_S]):
    __slots__ = ('__loop', '__future', )
    __match_args__ = ('_value_raw',)

    __loop: asyncio.AbstractEventLoop | None
    __future: asyncio.Future[_S] | None

    def __init__(self):
        self.__loop = None
        self.__future = None

    def __del__(self):
        if (future := self.__future) is None:
            return

        try:
            future.cancel()
        except RuntimeError:
            pass

    def __await__(self) -> Generator[Any, None, _S]:
        return self._future.__await__()

    def __repr__(self) -> str:
        return f'<{type(self).__name__}{self._format()} at {id(self):#x}>'

    def __str__(self) -> str:
        return f'<{type(self).__name__}{self._format()}>'

    @property
    def readonly(self) -> bool:
        """Returns true if an asyncio.Task will set the result, otherwise, the
        asyncio.Future can be set manually.
        """
        return isinstance(self._future, asyncio.Task)

    @property
    def is_done(self) -> bool:
        return self._future.done()

    @property
    def is_set(self) -> bool:
        future = self._future
        if not future.done():
            return False
        if future.cancelled():
            return False
        return future.exception() is None

    @property
    def is_error(self) -> bool:
        future = self._future
        if not future.done():
            return False
        if future.cancelled():
            return False
        if (exc := future.exception()) is None:
            return False

        return not isinstance(exc, asyncio.CancelledError)

    @property
    def is_cancelled(self) -> bool:
        future = self._future
        if not future.done():
            return False
        if future.cancelled():
            return True
        return isinstance(future.exception(), asyncio.CancelledError)
    
    @property
    def _loop(self) -> asyncio.AbstractEventLoop:
        if (loop := self.__loop) is None:
            if (future := self.__future) is not None:
                loop = future.get_loop()
            else:
                loop = asyncio.get_running_loop()

            self.__loop = loop

        return loop

    @property
    def _future(self) -> asyncio.Future[_S]:
        if (future := self.__future) is None:
            future = self.__future = self._loop.create_future()

        return future

    @_future.setter
    def _future(self, value: asyncio.Future[_S] | _S):
        if done := (future := self._future).done():
            future.result()

        if asyncio.isfuture(value):
            future = value
        else:
            if done:
                future = future.get_loop().create_future()
            future.set_result(cast(_S, value))

        self.__future = future

    @_future.deleter
    def _future(self):
        if (future := self.__future) is None or not future.done():
            return

        if future.cancelled():
            future.result()  # will raise asyncio.CancelledError

        self.__future = future.get_loop().create_future()

    @property
    def _value_raw(self) -> _S | BaseException | None:
        """For pattern matching."""
        fut = self._future
        if not fut.done():
            return None
        try:
            return fut.result()
        except (SystemExit, KeyboardInterrupt):
            raise
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

    def _cancel(self) -> bool:
        cancelled = self._future.cancel()

        self._on_cancel()

        return cancelled

    def _on_set(self, value: _S) -> None:
        ...

    def _on_error(self, exc: BaseException) -> None:
        ...

    def _on_cancel(self) -> None:
        ...

    def _format(self) -> str:
        fut = self._future

        if fut.done():
            return f'({self._value_raw!r})'

        return ''

    def __as_unset_future(self):
        future = self._future

        if isinstance(future, asyncio.Task):
            raise StateError(f'{self!r} is readonly')

        if future.done():
            current = future.result()
            raise StateError(f'{self!r} is already set: {current!r}')

        return future


class State(AsyncIterator[_S], StateBase[_S], Generic[_S]):
    __slots__ = (
        '_key',

        '_collections',
        '_producer',
        '_consumer',

        '_is_set',
        '_is_stopped',
        '_is_error',
        '_is_cancelled',

        '__waiters',
        '__waiter_counter',
    )

    # when provided, states are mapped before they're compared
    _key: Callable[[_S], Comparable]

    # change notification to e.g. StateVarTuple
    _collections: list[tuple[Any, StateCollection[Any, _S, Any]]]

    # see _set_from(), can be set only once
    _producer: Maybe[AsyncIterable[Any]]
    _consumer: Maybe[asyncio.Task[None | NoReturn]]

    def __init__(
        self,
        *,
        key: Callable[[_S], Comparable] = lambda s: s
    ) -> None:
        super().__init__()

        self._key = key

        self._consumer = Nothing
        self._producer = Nothing

        # TODO use weakref
        self._collections = []

        self._is_set = False
        self._is_stopped = False
        self._is_error = False
        self._is_cancelled = False

        self.__waiters = {}
        self.__waiter_counter = itertools.count(0).__next__

    def __del__(self):
        super().__del__()

        try:
            self._future.cancel()
            for waiter in self.__waiters.values():
                waiter.cancel()
        except RuntimeError:
            pass

        self._collections.clear()

    def __eq__(self, other: _S) -> bool:
        if not self.is_set:
            return False

        value = self._get()
        return bool(value is other or value == other)

    def __await__(self) -> Generator[Any, None, _S]:
        if not self.is_set:
            if (future := self._future).done():
                future.result()  # reraise if needed

            return self._wait_next().__await__()

        return super().__await__()

    async def __aiter__(self, *, buffer: int | None = 4) -> AsyncIterator[_S]:
        futures = collections.deque(maxlen=buffer)
        if self.is_set:
            futures.append(self._future)

        waiters = self.__waiters
        waiter_id = self.__waiter_counter()

        def _schedule_next(present=None):
            if present:
                if present.cancelled():
                    return
                if isinstance(present.exception(), StopAsyncIteration):
                    return

                loop = present.get_loop()
            else:
                loop = asyncio.get_running_loop()

            future = loop.create_future()
            future.add_done_callback(_schedule_next)

            assert waiter_id not in waiters or waiters[waiter_id].done()
            waiters[waiter_id] = future

            futures.append(future)

        try:
            _schedule_next()

            while not self.is_done:
                if not len(futures):
                    await asyncio.sleep(0)
                    assert len(futures)

                try:
                    yield await futures.popleft()
                except StopAnyIteration:
                    break
        finally:
            if waiter_id in waiters:
                del waiters[waiter_id]

    async def __anext__(self) -> _S:
        try:
            return await self._wait_next()
        except asyncio.CancelledError:
            raise StopAsyncIteration

    __hash__ = None  # type: ignore

    async def _wait_next(self) -> _S:
        waiters = self.__waiters
        waiter_id = self.__waiter_counter()

        future = waiters[waiter_id] = self._loop.create_future()

        try:
            return await future
        finally:
            del waiters[waiter_id]

    @property
    def readonly(self) -> bool:
        """Returns true if an asyncio.Task will set the result, otherwise, the
        asyncio.Future can be set manually.
        """
        return self._producer is not Nothing or super().readonly

    @property
    def is_set(self) -> bool:
        return self._is_set

    @property
    def is_done(self) -> bool:
        return self._is_stopped or self._is_error or self._is_cancelled

    @property
    def is_stopped(self) -> bool:
        return self._is_stopped

    @property
    def is_error(self) -> bool:
        return self._is_error

    @property
    def is_cancelled(self) -> bool:
        return self._is_cancelled

    @overload
    def map(self, function: Callable[[_S], Awaitable[_S]]) -> Self: ...
    @overload
    def map(self, function: Callable[[_S], _S]) -> Self: ...

    @overload
    def map(
        self,
        function: Callable[[_S], Awaitable[object]],
        cls: type[_ST],
        *cls_args: Any,
        **cls_kwargs: Any,
    ) -> _ST:
        ...

    @overload
    def map(
        self,
        function: Callable[[_S], object],
        cls: type[_ST],
        *cls_args: Any,
        **cls_kwargs: Any,
    ) -> _ST:
        ...

    def map(
        self,
        function: Callable[[_S], Awaitable[_RS]] | Callable[[_S], _RS],
        cls: type[_ST] | None = None,
        *cls_args: Any,
        **cls_kwargs: Any,
    ) -> _ST:
        """Create a new instance from this state, with the function applied to
        its value.

        The function can be either sync or async.
        Unless specified, the mapped state type will be type(self).
        """

        if cls_kwargs is None:
            cls_kwargs = {}
        if 'name' not in cls_kwargs:
            cls_kwargs['name'] = f'{function.__name__}({self})'

        res: _ST
        if cls is None:
            res = cast(_ST, type(self)(*cls_args, **cls_kwargs))
        else:
            res = cls(*cls_args, **cls_kwargs)

        if self.is_set:
            initial = function(self._get())
            if awaitable(initial):
                async def _set_after():
                    res._set(cast(_RS, await initial))

                asyncio.create_task(_set_after())
            else:
                res._set(cast(_RS, initial))

        res._set_from(amap_iter(function, self))

        return res

    async def _consume(self) -> None | NoReturn:
        assert self._producer is not Nothing
        try:
            async for state in self._producer:
                self._set_item(state)
        except (SystemExit, KeyboardInterrupt) as exc:
            self._raise(exc)
            raise
        except GeneratorExit:
            # don't attempt to "stop" if the loop was closed
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                self._cancel()
            else:
                self._stop()
        except StopAnyIteration:
            self._stop()
        except asyncio.CancelledError:
            self._cancel()
        except BaseException as exc:
            self._raise(exc)
        else:
            await asyncio.sleep(0)
            self._stop()

    @overload
    def _get(self, default: _T) -> _S | _T: ...
    @overload
    def _get(self, default: NothingType = ...) -> _S: ...

    def _get(self, default: Maybe[_T] = Nothing) -> _S | _T:
        if (future := self._future).done():
            try:
                return future.result()
            except (GeneratorExit, StopAsyncIteration, asyncio.CancelledError):
                if default is Nothing:
                    raise
                return default

        if default is Nothing:
            raise LookupError(repr(self))
        return default

    def _set_from(self, producer: AsyncIterable[Any]) -> None:
        if self._producer is not Nothing:
            raise StateError(f'{self!r} is already being set')

        self._producer = producer

        task_name = f'{self}.consumer'
        self._consumer = asyncio.create_task(self._consume(), name=task_name)

    def _set_item(self, value: Any):
        """Used by the consumer to set the next item"""
        self._set(cast(_S, value))

    def _set(self, value: _S, always: bool = False):
        if not always and self._equals(value):
            return 0

        del self._future
        cast(asyncio.Future[_S], self._future).set_result(value)

        self._on_set(value)
        self.__notify_waiters(value)

    def _clear(self) -> None:
        del self._future

        self._on_clear()

    def _stop(self) -> None:
        exc = StopAsyncIteration

        future = self._future
        if not future.done():
            future.set_exception(exc)

        self._on_stop()
        self.__notify_waiters(exc=exc)

    def _raise(self, exc: type[BaseException] | BaseException) -> None:
        if isinstance(exc, type):
            exc = exc()

        if isinstance(exc, asyncio.CancelledError):
            self._cancel()
        elif isinstance(exc, StopAsyncIteration):
            self._stop()
        else:
            del self._future
            cast(asyncio.Future[_S], self._future).set_exception(exc)

            self._on_error(exc)
            self.__notify_waiters(exc=exc)

    def _cancel(self) -> None:
        if self._is_cancelled:
            return

        self._future.cancel()

        self._on_cancel()
        self.__notify_waiters(cancel=True)

    def _on_set(self, state: _S) -> None:
        self._is_set = True

        for j, parent in self._collections:
            # noinspection PyProtectedMember
            parent._on_item_set(j, state)

    def _on_clear(self) -> None:
        assert not self._is_cancelled

        self._is_set = False
        self._is_error = False
        self._is_stopped = False

        for j, parent in self._collections:
            # noinspection PyProtectedMember
            parent._on_item_del(j)

    def _on_stop(self) -> None:
        self._is_stopped = True

        for j, parent in self._collections:
            # noinspection PyProtectedMember
            parent._on_item_stop(j)

    def _on_error(self, exc: BaseException) -> None:
        if isinstance(exc, asyncio.CancelledError):
            self._on_cancel()
        elif isinstance(exc, StopAnyIteration):
            self._on_stop()
        else:
            self._is_error = True

            for j, parent in self._collections:
                # noinspection PyProtectedMember
                parent._on_item_error(j, exc)

    def _on_cancel(self) -> None:
        self._is_cancelled = True

        for j, parent in self._collections:
            # noinspection PyProtectedMember
            parent._on_item_cancel(j)

        self._collections.clear()

    def _equals(self, state: _S) -> bool:
        """Returns True if set and the argument is equal to the current state"""
        future = self._future
        if not future.done():
            return False

        # raises exception if thrown or cancelled
        key = self._key(state)
        key_current = self._key(future.result())

        return key is key_current or key == key_current

    def _check(self) -> None | NoReturn:
        future = self._future
        if future.done():
            # raises after set_exception() or cancel()
            future.result()

        consumer = self._consumer
        if consumer is not Nothing and consumer.done():
            if consumer.exception() is not None:
                consumer.result()  # raises exception
            elif consumer.cancelled() and not self.is_set:
                consumer.result()  # raises asyncio.CancelledError

    def _check_next(self) -> None | NoReturn:
        consumer = self._consumer
        if consumer is not Nothing and consumer.done():
            consumer.result()
            raise StopAsyncIteration

        self._check()
        if self.is_done:
            raise StopAsyncIteration

    def _ensure_mutable(self) -> None | NoReturn:
        if self.readonly:
            raise StateError(f'{self!r} is readonly')

    def __notify_waiters(self, result=None, exc=None, cancel=False,):
        for waiter in self.__waiters.values():
            if waiter.done():
                continue

            if cancel:
                waiter.cancel()
            elif exc is not None:
                waiter.set_exception(exc)
            else:
                waiter.set_result(result)

    def __get_fresh_future(self) -> asyncio.Future[_S]:
        del self._future
        return self._future


class StateCollection(State[_SS], Generic[_KT, _S, _SS]):
    @abc.abstractmethod
    def __iter__(self) -> Iterator[State[_S]]: ...

    @abc.abstractmethod
    def __contains__(self, item: object) -> bool: ...

    @abc.abstractmethod
    def _get_states(self) -> Mapping[_KT, State[_S]]: ...

    @overload
    @abc.abstractmethod
    def _get_data(self) -> _SS: ...
    @overload
    @abc.abstractmethod
    def _get_data(self, default: _SS = ...) -> _SS: ...
    @abc.abstractmethod
    def _get_data(self, default: Maybe[_S] = Nothing) -> _SS: ...

    def __len__(self) -> int:
        return len(self._get_states())

    def __getitem__(self, key: _KT) -> State[_S]:
        return self._get_states()[key]

    def __setitem__(self, key: _KT, value: _S) -> None:
        self._get_states()[key]._set(value)

    @property
    def readonly(self) -> bool:
        return super().readonly or any(
            s.readonly for s in self._get_states().values()
        )

    @property
    def any_done(self) -> bool:
        return any(s.is_done for s in self._get_states().values())

    @property
    def all_done(self) -> bool:
        return all(s.is_done for s in self._get_states().values())

    @property
    def any_set(self) -> bool:
        return any(s.is_set for s in self._get_states().values())

    @property
    def all_set(self) -> bool:
        return all(s.is_set for s in self._get_states().values())

    @property
    def any_stopped(self) -> bool:
        return any(s.is_stopped for s in self._get_states().values())

    @property
    def all_stopped(self) -> bool:
        return all(s.is_stopped for s in self._get_states().values())

    @property
    def any_error(self) -> bool:
        return any(s.is_error for s in self._get_states().values())

    @property
    def all_error(self) -> bool:
        return all(s.is_error for s in self._get_states().values())

    @property
    def any_cancelled(self) -> bool:
        return any(s.is_cancelled for s in self._get_states().values())

    @property
    def all_cancelled(self) -> bool:
        return all(s.is_cancelled for s in self._get_states().values())

    @overload
    def get(self, key: NothingType = ..., /) -> _SS: ...
    @overload
    def get(self, key: _KT, /) -> _S: ...
    @overload
    def get(self, key: _KT, /, default: _T = ...) -> _S | _T: ...

    def get(
        self,
        key: Maybe[_KT] = Nothing,
        /,
        default: Maybe[_T] = Nothing
    ) -> _SS | _S | _T:
        if key is Nothing:
            return self._get_data()

        if key not in (states := self._get_states()):
            if default is Nothing:
                raise KeyError(key)

            return default

        return states[key]._get(default)

    # Internal: following methods are called by a statevar after it was updated

    def _sync_soon(self):
        self._loop.call_soon(lambda: self._set(self._get_data()))

    # noinspection PyUnusedLocal
    def _on_item_set(self, item: _KT, value: _S) -> None:
        if self.all_set:
            self._sync_soon()

    # noinspection PyUnusedLocal
    def _on_item_del(self, item: _KT) -> None:
        if self.all_set:
            self._sync_soon()

    # noinspection PyUnusedLocal
    def _on_item_stop(self, item: _KT) -> None:
        self._loop.call_soon(self._stop)

    def _on_item_error(self, item: _KT, exc: BaseException) -> None:
        if isinstance(exc, (StopIteration, StopAsyncIteration, GeneratorExit)):
            self._on_item_stop(item)
            return
        if isinstance(exc, asyncio.CancelledError):
            self._on_item_cancel(item)
            return

        self._loop.call_soon(self._raise, exc)

    # noinspection PyUnusedLocal
    def _on_item_cancel(self, item: _KT) -> None:
        loop = self._loop
        if loop.is_closed():
            self._cancel()
        else:
            loop.call_soon(self._cancel)
