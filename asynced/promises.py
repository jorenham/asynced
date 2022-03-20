from __future__ import annotations

__all__ = ('Promise', 'PromiseE')

import asyncio
import concurrent.futures
import warnings
from typing import (
    Any,
    Awaitable, Callable,
    cast, Generator,
    Generic,
    Literal,
    TypeVar,
)

from . import asyncio_utils
from ._typing import Maybe, Nothing, TypeAlias


_T = TypeVar('_T')
_R = TypeVar('_R')
_E = TypeVar('_E')

_RT = TypeVar('_RT')
_ET = TypeVar('_ET')


PromiseState: TypeAlias = Literal[
    'pending',
    'fulfilled',
    'rejected',
    'cancelled',
]


class PromiseError(Exception):
    pass


class PromiseRejected(PromiseError):
    pass


class Promise(Generic[_R, _E]):
    __slots__ = ('__state', '__result', '__future', '__task')

    __state: PromiseState
    __result: Maybe[_R | _E]

    def __init__(
        self,
        coro_or_executor: Awaitable[_R] | Callable[
            [Callable[[_R], None], Callable[[_E], None]],
            Awaitable[_R | None] | _R | None
        ], /,
    ):
        self.__state = 'pending'

        coro: Awaitable[_R | None]
        if callable(coro_or_executor):
            fn = asyncio_utils.ensure_async_function(coro_or_executor)
            coro = fn(self._fulfill, self._reject)
        elif asyncio.iscoroutine(coro_or_executor):
            coro = coro_or_executor
        else:
            raise TypeError(
                f'{type(self).__name__} accepts either an awaitable object or '
                f'a function like `(resolve, reject) -> Awaitable[None] | None`'
            )

        self.__future = asyncio_utils.create_future()
        self.__result = Nothing

        self.__task = task = asyncio.create_task(coro)
        task.add_done_callback(self.__on_result)

    @property
    def _state(self) -> PromiseState:
        return self.__state

    @_state.setter
    def _state(self, state: PromiseState) -> None:
        if self.__state != 'pending':
            raise asyncio.InvalidStateError(self.__state)

        self.__state = state

    @property
    def _result(self) -> _R:
        if self.__result is Nothing:
            raise asyncio.InvalidStateError(self.__state)

        if self.__state == 'rejected':
            err = self.__result
            if isinstance(err, BaseException):
                raise err
            raise PromiseRejected(err) from None

        if self.__state == 'fulfilled':
            return cast(_R, self.__result)

        raise asyncio.InvalidStateError(self.__state)

    @_result.setter
    def _result(self, result: _R | _E) -> None:
        if self.__result is not Nothing:
            raise asyncio.InvalidStateError('result already set')

        state = self.__state
        if state not in ('fulfilled', 'rejected'):
            raise asyncio.InvalidStateError(state)

        self.__result = result

        fut = self.__future
        if state == 'fulfilled':
            fut.set_result(result)
        else:
            if isinstance(result, BaseException):
                fut.set_exception(result)
            else:
                fut.set_exception(PromiseRejected(result))

    def __await__(self) -> Generator[Any, None, _R]:
        return self.__future.__await__()

    __iter__ = __await__  # make compatible with 'yield from'.

    def __bool__(self) -> bool:
        return self.__state != 'pending'

    @classmethod
    def resolve(cls, result: _R) -> Promise[_R, None]:
        return Promise(lambda resolve, _: resolve(result))

    @classmethod
    def reject(cls, reason: _E) -> Promise[None, _E]:
        return Promise(lambda __, reject: reject(reason))

    def then(
        self,
        on_fulfilled: Callable[[_R], Awaitable[_RT] | _RT],
        /, *,
        executor: Maybe[concurrent.futures.Executor | None] = Nothing
    ) -> PromiseE[_RT]:
        async def _exec() -> _RT:
            value = await self
            result = on_fulfilled(value)

            return await result if asyncio.iscoroutine(result) else result

        return Promise(_exec())

    def except_(
        self,
        on_rejected: Callable[[_E], Awaitable[_RT] | _RT],
        /, *,
        executor: Maybe[concurrent.futures.Executor | None] = Nothing,
    ) -> PromiseE[_R | _RT]:
        async def _exec():
            try:
                return await self
            except Exception as e:
                result = on_rejected(value)

                return await result if asyncio.iscoroutine(result) else result

        return Promise(_exec())

    def finally_(
        self,
        on_finally: Callable[[], Awaitable[None] | None],
        /, *,
        executor: Maybe[concurrent.futures.Executor | None] = Nothing,
    ) -> PromiseE[_RT]:
        async def _exec():
            try:
                return await self
            finally:
                result = on_finally()
                if asyncio.iscoroutine(result):
                    await result

        return Promise(_exec())

    # Internal methods

    def _fulfill(self, value: _R) -> None:
        if self._state != 'pending':
            if value is not None:
                warnings.warn(RuntimeWarning(
                    f'ignoring resolved value {value!r} after {self!r} became '
                    f'{self._state}'
                ))
            return

        self._state = 'fulfilled'
        self._result = value

    def _reject(self, reason: _E) -> None:
        self._state = 'rejected'
        # mypy fails with property setters that don't match the getter
        self._result = reason  # type: ignore

    def _cancel(self, msg: str | None = None) -> None:
        if self._state != 'pending':
            warnings.warn(RuntimeWarning(
                f'ignoring cancellation of {self!r} after it became '
                f'{self._state}'
            ))
            return

        self._state = 'cancelled'
        self.__future.cancel(msg)

    def __on_result(self, task: asyncio.Task[_R | None]) -> None:
        # coro/executor callback
        assert task.done()

        try:
            result = task.result()
        except asyncio.CancelledError as e:
            msg = e.args[0] if e.args else None
            self._cancel(msg=msg)
        except Exception as e:
            if self.__state != 'pending':
                raise

            self._reject(cast(_E, e))
        else:
            if self.__state == 'pending':
                self._fulfill(cast(_R, result))

    def __chain(
        self,
        on_fulfilled: Callable[[_R], Awaitable[_RT] | _RT] | None = None,
        on_rejected: Callable[[_E], Awaitable[_RT] | _RT] | None = None,
        on_finally: Callable[[], Awaitable[None] | None] | None = None,
        *,
        executor: Maybe[concurrent.futures.Executor | None] = Nothing
    ) -> PromiseE[_R | _RT]:
        if on_fulfilled is not None:
            _on_fulfilled = asyncio_utils.ensure_async_function(
                on_fulfilled,
                executor=executor
            )
        else:
            _on_fulfilled = None

        if on_rejected is not None:
            _on_rejected = asyncio_utils.ensure_async_function(
                on_rejected,
                executor=executor,
            )
        else:
            _on_rejected = None

        if on_finally is not None:
            _on_finally = asyncio_utils.ensure_async_function(on_finally)
        else:
            _on_finally = None

        async def __executor() -> _R | _RT:
            try:
                value = await self
            except Exception as e:
                if _on_rejected is not None:
                    return await _on_rejected(e)
                raise
            else:
                if _on_fulfilled is not None:
                    return await _on_fulfilled(value)
                return value
            finally:
                if _on_finally is not None:
                    await _on_finally()

        return Promise(__executor())


PromiseE: TypeAlias = Promise[_R, Exception]
