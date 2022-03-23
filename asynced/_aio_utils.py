from __future__ import annotations

"""Various async(io)-related utility functions"""


__all__ = (
    'resume',
    'create_future',
    'create_task',
    'get_event_loop',
    'ensure_awaitable',
    'ensure_acallable',
    'wrap_async'
)

import asyncio
import functools
from typing import (
    Any,
    Awaitable,
    Callable,
    cast,
    Coroutine,
    Generator,
    Generic,
    overload,
    TypeVar,
)

from asynced._typing import (
    AnyCoro,
    awaitable,
    acallable,
    DefaultCoroutine,
    Maybe,
    Nothing,
    NothingType,
)

_T = TypeVar('_T', bound=object)
_T_co = TypeVar('_T_co', bound=object, covariant=True)


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


def create_task(
    coro: Coroutine[Any, Any, _T] | asyncio.Future[_T],
    *,
    name: str | None = None
) -> asyncio.Task[_T] | asyncio.Future[_T]:
    if isinstance(coro, asyncio.Task):
        if name and not coro.done() and coro.get_name().startswith('Task-'):
            # only replace name if not done and it has no custom name
            coro.set_name(name)
        return coro

    if isinstance(coro, asyncio.Future):
        return coro

    if asyncio.iscoroutine(coro):
        _coro = coro
    else:
        raise TypeError(f'a coroutine was expected, got {coro!r}')

    return get_event_loop().create_task(coro, name=name)


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


def wrap_async(fn: Callable[..., _T]) -> Callable[..., Awaitable[_T]]:
    @functools.wraps(fn)
    def _fn(*args: Any, **kwargs: Any) -> CoroWrapper[_T]:
        return CoroWrapper(fn(*args, **kwargs))

    return _fn


class CoroWrapper(DefaultCoroutine[_T_co], Generic[_T_co]):
    __slots__ = ('__wrapped__', )

    __wrapped__: _T_co

    def __init__(self, value: _T_co):
        self.__wrapped__ = value

    def __await__(self) -> Generator[Any, None, _T_co]:
        yield
        return self.__wrapped__
