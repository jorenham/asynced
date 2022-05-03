from __future__ import annotations

__all__ = ('StateBase', 'State', 'StateCollection')

import abc
import asyncio
import inspect
import itertools
from typing import (
    AsyncIterable,
    cast, Mapping, overload,
    Any,
    AsyncIterator,
    Awaitable,
    Callable,
    Collection,
    Coroutine,
    Final,
    Generator,
    Generic,
    Literal,
    NoReturn,
    TypeVar,
    Union,
)

from typing_extensions import ParamSpec, Self, TypeAlias

from . import amap_iter
from ._typing import awaitable, Comparable, Maybe, Nothing, NothingType
from .exceptions import StopAnyIteration, StateError

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
    __slots__ = ('__future', )
    __match_args__ = ('_value_raw',)

    __future: asyncio.Future[_S] | None

    def __init__(self):
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
    def _future(self) -> asyncio.Future[_S]:
        if (future := self.__future) is None:
            future = self.__future = asyncio.get_running_loop().create_future()

        return future

    @_future.setter
    def _future(self, future: asyncio.Future[_S]):
        if hasattr(self, '__future'):
            raise AttributeError(f"'_future' is already set")
        
        self.__future = future

    @property
    def _loop(self) -> asyncio.AbstractEventLoop:
        return self._future.get_loop()

    @property
    def _value_raw(self) -> _S | BaseException | None:
        """For pattern matching."""
        fut = self._future
        if not fut.done():
            return None
        try:
            return fut.result()
        except BaseException as exc:
            return exc
    
    def _set(self, value: _S) -> None:
        self.__as_unset_future().set_result(value)
        self._on_set(value)

    def _clear(self) -> None:
        """recreates the future if exists and set"""
        try:
            future = self.__future
        except AttributeError:
            return

        if future is None or not future.done():
            return

        future.result()  # in case of exceptions raises 'em

        self.__future = future.get_loop().create_future()

    def _raise(self, exc: BaseException) -> None:
        if isinstance(exc, asyncio.CancelledError):
            self._cancel()
            return

        self.__as_unset_future().set_exception(exc)
        self._on_error(exc)

    def _cancel(self) -> None:
        self._future.cancel()
        self._on_cancel()

    def _on_set(self, value: Maybe[object] = Nothing) -> None:
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
        '__propositions__',

        '_key',

        '_waiters',
        '_collections',
        '__waiter_counter',

        '_producer',
        '_consumer',

        '_is_set',
        '_is_stopped',
        '_is_error',
        '_is_cancelled',
    )

    # when provided, states are mapped before they're compared
    _key: Callable[[_S], Comparable]

    # future for each waiter in __await__
    _waiters: dict[int, asyncio.Future[_S]]

    # change notification to e.g. StateVarTuple
    _collections: list[tuple[Any, StateCollection[Any, _S, Any]]]

    # see _set_from(), can be set only once
    _producer: Maybe[AsyncIterable[_S]]
    _consumer: Maybe[asyncio.Task[None | NoReturn]]

    def __init__(
        self,
        *,
        key: Callable[[_S], Comparable] = lambda s: s
    ) -> None:
        super().__init__()

        self.__propositions__ = {}
        self._key = key

        self._waiters = {}
        self.__waiter_counter = itertools.count(0).__next__

        self._consumer = Nothing
        self._producer = Nothing

        # TODO use weakref
        self._collections = []

        self._is_set = False
        self._is_stopped = False
        self._is_error = False
        self._is_cancelled = False

    def __del__(self):
        super().__del__()

        try:
            for waiter in self._waiters.values():
                waiter.cancel()
        except RuntimeError:
            pass

        self.__propositions__.clear()
        self._collections.clear()

    def __eq__(self, other: State[_S] | _S) -> bool:
        if not self.is_set:
            return False

        value = self._get()
        return bool(value is other or value == other)

    def __aiter__(self) -> AsyncIterator[_S]:
        async def _aiter():
            present = self._future
            loop = present.get_loop()

            while not self.is_done:
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
        future = asyncio.get_running_loop().create_future()
        self._waiters[waiter_id] = future

        try:
            return await future
        except asyncio.CancelledError as exc:
            raise StopAsyncIteration from exc
        finally:
            del self._waiters[waiter_id]

    __hash__ = None  # type: ignore

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
                self._set(state)
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

    def _set(self, value: _S, always: bool = False) -> int:
        if not always and self._equals(value):
            return 0

        self.__get_fresh_future().set_result(value)
        self._on_set(value)

        notified = 1  # consider self as waiter

        for waiter in self._waiters.values():
            if not waiter.done():
                waiter.set_result(value)
                notified += 1

        return notified

    def _set_from(self, producer: AsyncIterable[_S]) -> None:
        if self._producer is not Nothing:
            raise StateError(f'{self!r} is already being set')

        self._producer = producer

        task_name = f'{self}.consumer'
        self._consumer = asyncio.create_task(self._consume(), name=task_name)

    def _set_apply(self, function: Callable[[_S], _S]) -> _S:
        """Apply the function to the current state value and return it."""
        # TODO support async functions
        state = function(self._get())
        self._set(state)
        return state

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

    def _on_set(self, value: Maybe[object] = Nothing) -> None:
        self._is_set = True

    def _on_stop(self) -> None:
        self._is_stopped = True

    def _on_error(self, exc: BaseException) -> None:
        if isinstance(exc, StopAnyIteration):
            self._on_stop()
            return

        self._is_error = True

    def _on_cancel(self) -> None:
        self._is_cancelled = True

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

    def __get_fresh_future(self) -> asyncio.Future[_S]:
        future = self._future
        if future.done():
            future = self._future = future.get_loop().create_future()
        return future


class StateCollection(State[_SS], Generic[_KT, _S, _SS]):
    __slots__ = ()

    @abc.abstractmethod
    def __iter__(self): ...

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
        return any(s.readonly for s in self._get_states().values())

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
    def get(self, key: _KT) -> _S: ...
    @overload
    def get(self, key: _KT, default: _T = ...) -> _S | _T: ...

    def get(self, key: _KT, default: Maybe[_T] = Nothing) -> _S | _T:
        states = self._get_states()
        if key not in states:
            if default is Nothing:
                raise KeyError(key)

            return default

        return states[key]._get(default)

    # noinspection PyUnusedLocal
    def _on_item_set(self, item: _KT, value: Maybe[_S] = Nothing) -> None:
        if self.all_set:
            self._set(self._get_data())

    # noinspection PyUnusedLocal
    def _on_item_del(self, item: _KT) -> None:
        if self.all_set:
            self._set(self._get_data())

    # noinspection PyUnusedLocal
    def _on_item_stop(self, item: _KT) -> None:
        self._stop()

    def _on_item_error(self, item: _KT, exc: BaseException) -> None:
        if isinstance(exc, (StopIteration, StopAsyncIteration, GeneratorExit)):
            self._on_item_stop(item)
            return
        if isinstance(exc, asyncio.CancelledError):
            self._on_item_cancel(item)
            return

        self._raise(exc)

    # noinspection PyUnusedLocal
    def _on_item_cancel(self, item: _KT) -> None:
        self._cancel()
