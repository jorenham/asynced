from __future__ import annotations

"""Various async(io)-related utility functions"""


__all__ = (
    'resume',
    'awaiter',
    'create_future',
    'create_task',
    'get_event_loop',
    'sync_to_async'
)

import asyncio
import functools
from typing import (
    Any,
    Callable,
    Coroutine,
    Generator,
    Generic,
    overload,
    TypeVar,
)
from typing_extensions import ParamSpec
from asynced._typing import (
    AsyncFunction,
    AwaitableN,
    awaitable,
    DefaultCoroutine,
    Maybe,
    Nothing,
    NothingType,
    Xsync,
)

_T = TypeVar('_T', bound=object)
_T_co = TypeVar('_T_co', bound=object, covariant=True)

_P = ParamSpec('_P')
_R = TypeVar('_R')


async def resume(result: _T | None = None) -> None:
    """Pass control back to the event loop"""
    return await asyncio.sleep(0, result)


async def awaiter(arg: Xsync[_T], *, max_awaits: int = 64) -> _T:
    """For e.g. converting simple awaitables to coroutines, flattening nested
    coros, or making a non-awaitable object awaitable."""
    res = arg

    awaits = 0
    while awaitable(res):
        res = await res

        awaits += 1
        if awaits >= max_awaits:
            raise RecursionError(f'awaitables are nested >{max_awaits} deep')

    if not awaits:
        await resume()

    return res


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


def sync_to_async(fn: Callable[_P, _R]) -> AsyncFunction[_P, _R]:
    """Create a (blocking!) async function from a sync function"""

    @functools.wraps(fn)
    async def _fn(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        return await resume(fn(*args, **kwargs))

    return _fn


class CoroWrapper(DefaultCoroutine[_T_co], Generic[_T_co]):
    __slots__ = ('__wrapped__', )

    __wrapped__: _T_co

    def __init__(self, value: _T_co):
        self.__wrapped__ = value

    def __await__(self) -> Generator[Any, None, _T_co]:
        yield
        return self.__wrapped__
