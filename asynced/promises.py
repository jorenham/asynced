from __future__ import annotations

__all__ = (
    'Promise',
    'PromiseIterator',
)

import asyncio
from typing import (
    Any,
    AsyncIterator,
    Coroutine,
    Generator,
    Generic,
    NoReturn,
    TypeVar,
)
from typing_extensions import Self

from ._aio_utils import create_task, wrap_async
from ._typing import AFunc1, DefaultCoroutine, Func1
from .interfaces import Catchable, Mappable

_T = TypeVar('_T')
_T_co = TypeVar('_T_co', covariant=True)

_R = TypeVar('_R')
_E = TypeVar('_E', bound=BaseException)


class Promise(DefaultCoroutine[_T], Mappable[_T], Catchable[_T], Generic[_T]):
    """Thin wrapper around asyncio.Task, inspired by javascript promises, and
    function programming in general."""

    __slots__ = ('_task', )

    _task: asyncio.Future[_T]

    def __init__(self, coro: Coroutine[Any, Any, _T] | asyncio.Future[_T], /):
        if asyncio.iscoroutine(coro):
            task = create_task(coro)
        elif isinstance(coro, Promise):
            task = coro._task
        elif isinstance(coro, asyncio.Future):
            task = coro
        else:
            raise TypeError(f'a coroutine was expected, got {coro!r}')

        self._task = task
    
    def __await__(self) -> Generator[Any, None, _T]:
        return self._task.__await__()

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

    # Synchrounous inspection properties

    @property
    def is_pending(self) -> bool:
        """True if there's no result is available yet."""
        return not self._task.done()

    @property
    def is_fulfilled(self) -> bool:
        """True if the coro finished without raising."""
        t = self._task
        return t.done() and not t.cancelled() and t.exception() is None

    @property
    def is_rejected(self) -> bool:
        """True if the coro raised an exception."""
        t = self._task
        return t.done() and not t.cancelled() and t.exception() is not None

    @property
    def is_cancelled(self) -> bool:
        """True if """
        return self._task.cancelled()

    @property
    def result(self) -> _T | NoReturn:
        """Return the fulfilled result of this promise.

        If the promise is cancelled, raises asyncio.CancelledError.
        If the promise is pending, raises asyncio.InvalidStateError.
        If the promise is rejected, this exception is raised.
        """
        return self._task.result()

    @property
    def exception(self) -> BaseException | None:
        """Return the rejection exception of this promise.

        If the promise is cancelled, raises asyncio.CancelledError.
        If the promise is pending, raises asyncio.InvalidStateError.
        If the promise is fulfilled, returns None.
        """
        return self._task.exception()

    @property
    def _loop(self) -> asyncio.AbstractEventLoop:
        return self._task.get_loop()

    def map(self, func: Func1[_T, _R], /) -> Self[_R]:
        """When this promise resolves, this funcion is called with the
        result as only argument, and the return value or raised exception will
        be used to resolve or reject the returned promise.
        """
        return self.amap(wrap_async(func))

    def amap(self, afunc: AFunc1[_T, _R], /) -> Self[_R]:
        """Like .map(), but for an async function, or a function that returns
        an awaitable.
        """
        async def _amap() -> _R:
            return await afunc(await self._task)

        return Promise(_amap())

    def catch(
        self,
        exc_typ: type[_E] | tuple[type[_E], ...],
        handler: Func1[[_E], _R],
        /,
    ) -> Promise[_T | _R]:
        """When the provided exception type is raised withing this promise,
        the function is called with the exception as argument. In turn, the
        new promise resolves to the returned value, or is rejected if the
        function (re)raises.
        """
        return super().catch(exc_typ, handler)

    def acatch(
        self,
        exc_typ: type[_E] | tuple[type[_E], ...],
        handler: AFunc1[[_E], _R],
        /,
    ) -> Promise[_T | _R]:
        """Like .catch(), but for async handlers."""
        async def _catch() -> _T | _R:
            try:
                return await self._task
            except exc_typ as exc:
                return await handler(exc)

        return Promise(_catch())


class PromiseIterator(
    AsyncIterator[_T],
    Mappable[_T],
    Catchable[_T],
    Generic[_T],
):
    __slots__ = ('_iterator', '__p_next')

    _iterator: AsyncIterator[_T]
    __p_next: Promise[_T] | None

    def __init__(self, iterator: AsyncIterator[_T], /):
        self._iterator = iterator
        self.__p_next = None

    def __aiter__(self) -> PromiseIterator[_T]:
        it_prev = self._iterator
        it = it_prev.__aiter__()

        if it is it_prev:
            return self

        return type(self)(it)

    def __anext__(self) -> Promise[_T]:
        if (p := self.__p_next) is None:
            p = self.__schedule_anext()

        return p

    def map(self, func: Func1[_T, _R], /) -> PromiseIterator[_R]:
        """Maps each iterated promise and returns a new PromiseIterator."""
        return super().map(func)

    def amap(self, afunc: AFunc1[_T, _R], /) -> PromiseIterator[_R]:
        """Maps each iterated promise asynchronously, and returns a new
        PromiseIterator."""
        async def _ait() -> AsyncIterator[_R]:
            async for value in self:
                yield await afunc(value)

        return PromiseIterator(_ait())

    def catch(
        self,
        exc_typ: type[_E] | tuple[type[_E], ...],
        handler: Func1[[_E], _R],
        /,
    ) -> PromiseIterator[_T | _R]:
        """Maps the exception type to a value when raised during iteration."""
        return super().catch(exc_typ, handler)

    def acatch(
        self,
        exc_typ: type[_E] | tuple[type[_E], ...],
        ahandler: AFunc1[[_E], _R],
        /,
    ) -> PromiseIterator[_T | _R]:
        """Like .catch(), but for async handlers."""

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
        self.__p_next = p = (
            Promise(self._iterator.__anext__())
            .catch(Exception, self.__on_anext_exception)
            .map(self.__on_anext_result)
        )
        return p

    def __on_anext_exception(self, exc: Exception) -> NoReturn:
        if not isinstance(exc, StopAsyncIteration):
            self.__p_next = None
        raise exc

    def __on_anext_result(self, result: _T) -> _T:
        """Internal: result callback of the wrapped anext task."""
        self.__p_next = None
        return result
