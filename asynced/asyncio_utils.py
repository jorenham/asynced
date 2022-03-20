from __future__ import annotations

__all__ = (
    'create_future',
    'ensure_event_loop',
    'wrap_coro',
    'wrap_coro_function',
)

import asyncio
import functools
from typing import Awaitable, Callable, TypeVar, Union

from asynced._typing import MaybeCoro

_T = TypeVar('_T')


def create_future() -> asyncio.Future:
    return ensure_event_loop().create_future()


def ensure_event_loop() -> asyncio.AbstractEventLoop:
    """Return the running event loop if exists, otherwise creates a new one.
    """
    return asyncio.get_event_loop_policy().get_event_loop()


async def wrap_coro(obj: MaybeCoro[_T], /) -> _T:
    if hasattr(obj, '__await__') or asyncio.iscoroutine(obj):
        return await obj

    await asyncio.sleep(0)  # avoid event loop congestion
    return obj


def wrap_coro_function(
    fn: Callable[..., MaybeCoro[_T]],
    /,
) -> Callable[..., Awaitable[_T]]:
    if not callable(fn):
        raise TypeError(f'{fn!r} is not callable')

    if asyncio.iscoroutinefunction(fn):
        return fn

    @functools.wraps(fn)
    async def _wrapper(*args, **kwargs):
        return await wrap_coro(fn(*args, **kwargs))

    return _wrapper
