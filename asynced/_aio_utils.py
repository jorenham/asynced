from __future__ import annotations

"""Various async(io)-related utility functions"""


__all__ = (
    'resume',
    'get_event_loop',
    'ensure_awaitable',
    'create_future',
    'ensure_acallable',
)

import asyncio
import functools
from typing import (
    Any,
    Callable,
    cast,
    Coroutine,
    overload,
    TypeVar,
)

from asynced._typing import (
    AnyCoro,
    awaitable,
    acallable,
    Maybe,
    Nothing,
    NothingType,
)

_T = TypeVar('_T', bound=object)


async def resume() -> None:
    """Pass control back to the event loop"""
    await asyncio.sleep(0)


@overload
def create_future(
    result: _T, exception: NothingType = ...
) -> asyncio.Future[_T]: ...


@overload
def create_future(
    result: NothingType = ..., exception: NothingType = ...
) -> asyncio.Future[Any]: ...


def create_future(
    result: Maybe[_T] = Nothing,
    exception: Maybe[BaseException] = Nothing
) -> asyncio.Future[_T] | asyncio.Future[Any]:
    """Shorthand for asyncio.get_event_loop().create_future()"""
    fut = get_event_loop().create_future()
    if result is not Nothing:
        fut.set_result(result)
    if exception is not Nothing:
        fut.set_exception(exception)
    return fut


def get_event_loop() -> asyncio.AbstractEventLoop:
    """Return the running event loop if exists, otherwise creates a new one."""
    return asyncio.get_event_loop_policy().get_event_loop()


def ensure_awaitable(obj: AnyCoro[_T] | _T, /) -> AnyCoro[_T]:
    """Coroutines and awaitables are returned directly, otherwise a settled
    future of the object is returned."""
    if awaitable(obj):
        return obj

    return create_future(cast(_T, obj))


def ensure_acallable(
    fn: Callable[..., Coroutine[Any, Any, _T]] | Callable[..., _T],
    /,
) -> Callable[..., Coroutine[Any, Any, _T]]:
    """Ensure that the callable arg returns an awaitable. Coroutine functions
    are returned as-is.
    """
    if not callable(fn):
        raise TypeError(f'{fn!r} is not callable')

    if acallable(fn):
        return fn

    @functools.wraps(fn)
    async def _wrapper(*args: Any, **kwargs: Any) -> _T:
        return await ensure_awaitable(fn(*args, **kwargs))

    return _wrapper
