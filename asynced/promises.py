from __future__ import annotations

__all__ = (
    'Promise',
    'PromiseFuture',
    'PromiseIterator',
)

import asyncio
import warnings
from types import TracebackType
from typing import (
    Any,
    AsyncIterable, AsyncIterator,
    cast,
    Coroutine,
    Generator,
    Generic,
    Iterable, Iterator,
    NoReturn,
    TypeVar,
    Union,
)
from typing_extensions import Self, TypeAlias, TypeVarTuple

from ._aio_utils import awaiter, create_future, create_task
from ._typing import (
    AnyCoro, Catchable as CatchableT,
    DefaultCoroutine,
    Throwable,
    XsyncCallable,
)
from .interfaces import Catchable, Mappable, Thenable

_T = TypeVar('_T')
_T_co = TypeVar('_T_co', covariant=True)

_Ts = TypeVarTuple('_Ts')

_T1 = TypeVar('_T1')
_T2 = TypeVar('_T2')
_T3 = TypeVar('_T3')

_R = TypeVar('_R')
_E = TypeVar('_E', bound=Exception)

_P = TypeVar('_P', bound='Promise')
_PS = TypeVar('_PS', bound='PromiseIterator')

_CoroT: TypeAlias = Union[AnyCoro[_T], asyncio.Task[_T], 'Promise[_T]']


class Promise(DefaultCoroutine[_T], Thenable[_T], Catchable[_T], Generic[_T]):
    """Thin wrapper around asyncio.Task, inspired by javascript promises, and
    function programming in general."""

    __slots__ = ('_task', )

    _task: asyncio.Task[_T]

    def __init__(self, coro: _CoroT[_T], /) -> None:
        if isinstance(coro, Promise):
            task = coro._task
        elif isinstance(coro, asyncio.Task):
            task = coro
        elif asyncio.iscoroutine(coro):
            task = create_task(coro)
        else:
            raise TypeError(f'a coroutine was expected, got {coro!r}')

        self._task = task
    
    def __await__(self) -> Generator[Any, None, _T]:
        return self._task.__await__()

    @property
    def _loop(self) -> asyncio.AbstractEventLoop:
        return self._task.get_loop()

    @property
    def _coro(self) -> Coroutine[Any, Any, _T]:
        return cast(Coroutine[Any, Any, _T], self._task.get_coro())

    @classmethod
    def as_fulfilled(cls, value: _T):
        """Create an already fulfilled promise from the value."""
        async def _coro():
            return value

        return cls(_coro())

    @classmethod
    def as_rejected(cls, exc: Exception | type[Exception]):
        """Create an already rejected promise from the exception."""
        if not (isinstance(exc, Exception) or issubclass(exc, Exception)):
            raise TypeError('exception must derive from Exception')

        async def _coro():
            raise exc

        return cls(_coro())

    @classmethod
    def gather(cls, *coros_or_promises: _CoroT[_T]) -> PromiseTuple[_T]:
        return PromiseTuple(coros_or_promises)

    # Coroutine emulation

    def send(self, value: Any) -> Any:
        return self._coro.send(value)

    # noinspection PyMethodMayBeStatic
    def throw(
        self,
        typ: type[BaseException] | BaseException,
        val: BaseException | object | None = None,
        tb: TracebackType | None = None
    ) -> Any:
        return self._coro.throw(typ, val, tb)

    def close(self) -> None:
        return self._coro.close()

    # Synchrounous inspection

    def done(self) -> bool:
        """True if fulfilled, rejected, or cancelled. False if pending"""
        return self._task.done()

    def fulfilled(self) -> bool:
        """True if the coro finished without raising."""
        t = self._task
        return t.done() and not t.cancelled() and t.exception() is None

    def rejected(self) -> bool:
        """True if the coro raised an exception."""
        t = self._task
        return t.done() and not t.cancelled() and t.exception() is not None

    def cancelled(self) -> bool:
        """True if cancelled."""
        return self._task.cancelled()

    def result(self) -> _T | NoReturn:
        """Return the fulfilled result of this promise.

        If the promise is cancelled, raises asyncio.CancelledError.
        If the promise is pending, raises asyncio.InvalidStateError.
        If the promise is rejected, this exception is raised.
        """
        return self._task.result()

    def exception(self) -> BaseException | None:
        """Return the rejection exception of this promise.

        If the promise is cancelled, raises asyncio.CancelledError.
        If the promise is pending, raises asyncio.InvalidStateError.
        If the promise is fulfilled, returns None.
        """
        return self._task.exception()

    # Chaining methods

    def then(self, on_fulfilled: XsyncCallable[[_T], _R], /) -> Promise[_R]:
        """Return a new promise that resolves to the return value of the
        function, which is called when this promise resolves with its result.
        """
        async def _on_fulfilled() -> _R:
            result = await self
            return await awaiter(on_fulfilled(result))

        return Promise(_on_fulfilled())

    def catch(
        self,
        exc_typ: CatchableT[_E],
        on_rejected: XsyncCallable[[_E], _R],
        /,
    ) -> Promise[_T | _R]:
        """When the provided exception type is raised withing this promise,
        the function is called with the exception as argument. In turn, the
        new promise resolves to the returned value, or is rejected if the
        function (re)raises.
        """
        async def _on_fulfilled() -> _R:
            try:
                return await self
            except exc_typ as exc:
                return await awaiter(on_rejected(exc))

        return Promise(_on_fulfilled())


# TODO inherit from BasePromise that doesn't have catch() and exception()
class PromiseTuple(
    Promise[tuple[_T, ...]],
    Mappable[_T],
    Catchable[_T],
    Generic[_T]
):
    __slots__ = ('_promises', )

    _promises: tuple[Promise[_T], ...]

    def __init__(self, coros: Iterable[_CoroT[_T]], /) -> None:
        self._promises = tuple(
            coro if isinstance(coro, Promise) else Promise(coro)
            for coro in coros
        )
        super().__init__(awaiter(asyncio.gather(*self._promises)))

    def __len__(self) -> int:
        return len(self._promises)

    def __getitem__(self, index: int | slice) -> Promise[_T]:
        if isinstance(index, int):
            return self._promises[index]

    def __add__(self, other: PromiseTuple[_R]) -> PromiseTuple[_T | _R]:
        if not isinstance(other, PromiseTuple):
            return NotImplemented
        return PromiseTuple(self._promises + other._promises)

    def __mul__(self, other) -> PromiseTuple[_T]:
        if not isinstance(other, int):
            return NotImplemented

        return PromiseTuple(self._promises * other)

    def __rmul__(self, other) -> PromiseTuple[_T]:
        return self.__mul__(other)

    def __iter__(self) -> tuple[Promise[_T], ...]:
        return self._promises

    def __aiter__(self) -> PromiseIterator[_T]:
        async def _aiter():
            for promise in self._promises:
                yield promise

    def __hash__(self) -> int:
        return hash(self._promises)

    @property
    def _coros(self) -> tuple[Coroutine[Any, Any, _T], ...]:
        return tuple(p._coro for p in self._promises)

    # Chaining methods

    def map(
        self,
        on_each_fulfilled: XsyncCallable[[_T], _R],
        /
    ) -> PromiseTuple[_R]:
        """Returns a new PromiseTuple that have been mapped with .then(...) for
        each individual promise.
        """
        return PromiseTuple(p.then(on_each_fulfilled) for p in self)

    def catch(
        self,
        exc_typ: CatchableT[_E],
        on_each_fulfilled: XsyncCallable[[_E], _R],
        /
    ) -> PromiseTuple[_T | _R]:
        return PromiseTuple(p.catch(on_each_fulfilled) for p in self)


class PromiseFuture(Promise[_T]):
    __slots__ = ('_future', )

    _future: asyncio.Future[_T]

    def __init__(self):
        self._future = create_future()
        super().__init__(awaiter(self._future))

        self._task.add_done_callback(self.__on_task_done)

    def fulfill(self, result: _T):
        """Mark the promise as fulfilled and set its result.

        If the promise isn't pending when this method is called, raises
        asyncio.InvalidStateError.
        """
        self._future.set_result(result)

    def reject(self, exc: Throwable):
        if isinstance(exc, type) and issubclass(exc, BaseException):
            exc = exc()
        if not isinstance(exc, BaseException):
            raise TypeError('exception must derive from Exception')

        if isinstance(exc, asyncio.CancelledError):
            self.cancel()

        self._future.set_exception(exc)

    def cancel(self, msg: str | None = None) -> bool:
        """Cancel the promise.

        If the promise not pending, return False.  Otherwise, change the
        promise's state to cancelled and return True.
        """
        return self._future.cancel(msg)

    def __on_task_done(self, task: asyncio.Task[_T]):
        future = self._future
        if future.done():
            return

        # when the future was bypassed, complain, and inversely sync
        warnings.warn(RuntimeWarning(f'{self!r} task done before the future'))

        if task.cancelled():
            self._future.cancel()
        elif (exc := task.exception()) is not None:
            self._future.set_exception(exc)
        else:
            self._future.set_result(task.result())


class PromiseIterator(
    Iterator[Promise[_T]],
    AsyncIterator[_T],
    Mappable[_T],
    Catchable[_T],
    Generic[_T],
):
    __slots__ = ('_iterator', '__p_next')

    _iterator: AsyncIterator[_T] | Iterator[Promise[_T]]
    __p_next: Promise[_T] | None

    def __init__(self, it: AsyncIterable[_T] | Iterable[Promise[_T]], /):
        if hasattr(it, '__aiter__') and not hasattr(it, '__anext__'):
            self._iterator = it.__aiter__()
        elif hasattr(it, '__iter__') and not hasattr(it, '__next__'):
            self._iterator = it.__iter__()
        self.__p_next = None

    def __aiter__(self: _PS) -> _PS:
        it_prev = self._iterator
        it = it_prev.__aiter__()

        if it is it_prev:
            return self

        return type(self)(it)

    def __anext__(self) -> Promise[_T]:
        if (p := self.__p_next) is None:
            p = self.__schedule_anext()

        return p

    __iter__ = __aiter__
    __next__ = __anext__

    def map(self, func: XsyncCallable[[_T], _R], /) -> PromiseIterator[_R]:
        # TODO
        ...

    def catch(
        self,
        exc_typ: CatchableT[_E],
        ahandler: XsyncCallable[[_E], _R],
        /,
    ) -> PromiseIterator[_T | _R]:
        """Maps the exception type to a value when raised during iteration."""

        async def _catcher() -> AsyncIterator[_T | _R]:
            it = self.__aiter__()
            __anext__ = type(it).__anext__
            while True:
                try:
                    yield await __anext__(it)
                except StopAsyncIteration:
                    break
                except exc_typ as exc:
                    yield await ahandler(exc)

        return PromiseIterator(_catcher())

    def __schedule_anext(self) -> Promise[_T]:
        coro = self._iterator.__anext__()
        p0 = coro if isinstance(coro, Promise) else Promise

        p = p0.catch(Exception, self.__on_exception).map(self.__on_result)
        self.__p_next = p
        return p

    def __on_exception(self, exc: Exception) -> NoReturn:
        if not isinstance(exc, StopAsyncIteration):
            self.__p_next = None
        raise exc

    def __on_result(self, result: _T) -> _T:
        """Internal: result callback of the wrapped anext task."""
        self.__p_next = None
        return result
