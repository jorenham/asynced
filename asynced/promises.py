from __future__ import annotations

__all__ = ('Promise', 'PromiseE')

import asyncio
import sys
from types import TracebackType
from typing import (
    Any,
    Awaitable,
    Callable,
    Final,
    Generator,
    Generic,
    Literal,
    NoReturn,
    Optional,
    TypeVar,
)

from . import asyncio_utils
from ._typing import MaybeCoro, TypeAlias


_R = TypeVar('_R')
_E = TypeVar('_E', bound=Optional[Exception])

_RT = TypeVar('_RT')
_ET = TypeVar('_ET', bound=Exception)


PromiseState: TypeAlias = Literal[
    'pending',
    'fulfilled',
    'rejected',
    'cancelled',
]

PENDING: Final[PromiseState] = 'pending'
FULFILLED: Final[PromiseState] = 'fulfilled'
REJECTED: Final[PromiseState] = 'rejected'
CANCELLED: Final[PromiseState] = 'cancelled'


# intern the states
def __intern_states() -> None:
    for state in [PENDING, FULFILLED, REJECTED, CANCELLED]:
        sys.intern(state)


__intern_states()


class Promise(Generic[_R, _E]):
    __slots__ = ('__state', '__result', '__task')

    __state: PromiseState
    __result: asyncio.Future[_R]
    __task: asyncio.Task[_R]

    def __init__(self, coro: Awaitable[_R]):
        self.__state = PENDING
        self.__result = asyncio_utils.create_future()
        self.__task = asyncio.ensure_future(coro)

        self.__task.add_done_callback(self.__on_result)

    def __await__(self) -> Generator[Any, None, _R]:
        return self.__result.__await__()

    # collection.abc.Coroutine emulation: otherwise asyncio.iscoroutine fails

    def send(self, value: Any) -> NoReturn:
        raise NotImplementedError

    def throw(self, typ: Any, val: None = None, tb: None = None) -> NoReturn:
        raise NotImplementedError

    def close(self) -> NoReturn:
        raise NotImplementedError

    def __bool__(self) -> bool:
        """Return True if done"""
        return self.__state != 'pending'

    @classmethod
    def resolve(cls, result: _RT) -> Promise[_RT, None]:
        """Returns a new Promise that has resolved to the given result."""
        async def _resolve() -> _RT:
            return result

        return Promise(_resolve())

    @classmethod
    def reject(cls, exc: _ET) -> Promise[NoReturn, _ET]:
        """Returns a new Promise that is rejected with the given error."""
        async def _reject() -> NoReturn:
            raise exc

        return Promise(_reject())

    def then(
        self,
        on_fulfilled: Callable[[_R], MaybeCoro[_RT]],
        /,
    ) -> PromiseE[_RT]:
        """When this promise resolves, this funcion is called with the
        result as only argument, and the return value or raised exception will
        be used to resolve or reject the new promise that is returned.

        The function can be sync or async. Only exceptions that derive from
        Exception will propagate to the new promise silently, those that are
        derive from BaseException only will be reraised on the spot.
        """
        async def _exec() -> _RT:
            value = await self
            result: MaybeCoro[_RT] = on_fulfilled(value)
            return await asyncio_utils.wrap_coro(result)

        return Promise(_exec())

    def except_(
        self,
        on_rejected: Callable[[Exception], MaybeCoro[_RT]],
        /,
    ) -> PromiseE[_R | _RT]:
        """When this promise is rejected, this function is called with the
        exception as only argument. In turn, the returned promise resolves to
        the returned value, or is rejected if the function reraises.

        The function can be sync or async. Exceptions that only derive from
        BaseException will not propagate.
        """
        async def _exec() -> _R | _RT:
            try:
                return await self
            except Exception as exc:
                result = on_rejected(exc)
                return await asyncio_utils.wrap_coro(result)

        return Promise(_exec())

    catch = except_  # js-style alias

    def finally_(
        self,
        on_finally: Callable[[], MaybeCoro[None]],
        /,
    ) -> Promise[_R, _E]:
        """The given function will be called if resolved or rejected in the
        returned promise.

        The function can be sync or async. If the wrapped coroutine is
        cancelled or rejected with an exception that only derives from
        BaseException, this 'finally' handler will not be called.
        """
        async def _exec() -> _R:
            try:
                return await self
            finally:
                await asyncio_utils.wrap_coro(on_finally())

        return Promise(_exec())

    @property
    def _state(self) -> PromiseState:
        """The promise state: pending, fulfilled, rejected or cancelled."""
        return self.__state

    @property
    def _result(self) -> _R | NoReturn:
        """Return the result if fulfilled.

        Raises asyncio.InvalidStateError if pending, raises
        asyncio.CancelledError if the wrapped coro was cancelled,
        or if the coro raised an exception, raises that.
        """
        return self.__result.result()

    def __on_result(self, task: asyncio.Task[_R]) -> None:
        """Internal: done callback of wrapped coro"""
        assert task.done()
        fut = self.__result
        assert not fut.done()
        assert self.__state == 'pending'

        try:
            result = task.result()
        except asyncio.CancelledError as exc:
            self.__state = CANCELLED
            fut.cancel(exc.args[0] if exc.args else None)
        except Exception as exc:
            self.__state = REJECTED
            fut.set_exception(exc)
        except BaseException as exc:
            self.__state = REJECTED
            fut.set_exception(exc)
            raise
        else:
            self.__state = FULFILLED
            fut.set_result(result)


PromiseE: TypeAlias = Promise[_R, Exception]
