from __future__ import annotations

__all__ = (
    'create_future',
    'ensure_async_function',
    'ensure_event_loop',
)

import asyncio
import concurrent.futures
import functools
from typing import Awaitable, Callable, TypeVar

from asynced._typing import Maybe, Nothing

_T = TypeVar('_T')


def create_future() -> asyncio.Future:
    return ensure_event_loop().create_future()


def ensure_async_function(
    fn: Callable[..., _T | Awaitable[_T]],
    *,
    executor: Maybe[concurrent.futures.Executor | None] = Nothing
) -> Callable[..., Awaitable[_T]]:
    if not callable(fn):
        raise TypeError(f'{fn!r} is not callable')

    if asyncio.iscoroutinefunction(fn):
        return fn

    @functools.wraps(fn)
    async def _wrapper(*args):

        if executor is not Nothing:
            loop = asyncio.get_event_loop()
            res = await loop.run_in_executor(executor, fn, *args)
        else:
            res = fn(*args)
            await asyncio.sleep(0)

        if hasattr(res, '__await__') or asyncio.iscoroutine(res):
            return await res

        return res


def ensure_event_loop() -> asyncio.AbstractEventLoop:
    """Return the running event loop if exists, otherwise creates a new one.
    """
    return asyncio.get_event_loop_policy().get_event_loop()

