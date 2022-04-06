from __future__ import annotations

from asynced import StateVar
from asynced.exceptions import StopAnyIteration

"""Various async(io)-related utility functions"""


__all__ = (
    'resume',
    'awaiter',
    'race',
    'create_future',
    'create_task',
    'get_event_loop',
    'sync_to_async'
)

import asyncio
import functools
from typing import (
    Any,
    AsyncIterable, AsyncIterator, Awaitable,
    Callable,
    cast,
    Coroutine,
    Generator,
    Generic,
    overload,
    TypeVar,
)
from typing_extensions import ParamSpec
from asynced._typing import (
    AsyncFunction,
    awaitable,
    DefaultCoroutine,
    Maybe,
    Nothing,
    NothingType,
)

_T = TypeVar('_T')
_T_co = TypeVar('_T_co', covariant=True)

_P = ParamSpec('_P')
_R = TypeVar('_R')


@overload
async def resume() -> None: ...
@overload
async def resume(result: _T = ...) -> _T: ...


async def resume(result: _T | None = None) -> _T | None:
    """Pass control back to the event loop"""
    return await asyncio.sleep(0, result)


async def awaiter(
    arg: Awaitable[Awaitable[_T]] | Awaitable[_T] | _T,
    *,
    max_awaits: int = 4
) -> _T:
    """For e.g. converting simple awaitables to coroutines, flattening nested
    coros, or making a non-awaitable object awaitable."""

    if max_awaits <= 0:
        raise RecursionError(f'max recursion depth reached in nested awaitable')

    if not awaitable(arg):
        return cast(_T, arg)

    return await awaiter(await arg, max_awaits=max_awaits-1)


async def race(
    *args: AsyncIterable[_T],
    yield_exceptions: bool = False,
) -> AsyncIterator[tuple[int, _T | BaseException]]:
    """Yields the argument index and the item of each first next of the
    iterators.
    """
    if not args:
        raise TypeError('race() must have at least one argument.')

    itrs = [aiter(arg) for arg in args]

    tasks: dict[str, tuple[int, asyncio.Task[_T]]] = {}
    for i, itr in enumerate(itrs):
        task = asyncio.create_task(itr.__anext__())
        tasks[task.get_name()] = i, task

    while tasks:
        done, pending = await asyncio.wait(
            [task for i, task in tasks.values()],
            return_when=asyncio.FIRST_COMPLETED
        )
        if not done:
            # no done; exception might be raised in the pending ones
            for task in pending:
                if task.done():
                    task.result()
            assert False, 'no tasks completed and no exceptions were raised'
            break  # noqa

        for task in done:
            assert task.done()

            name = task.get_name()
            i = tasks.pop(name)[0]

            try:
                yield i, task.result()
            except (StopAsyncIteration, asyncio.CancelledError) as exc:
                if yield_exceptions:
                    yield i, exc
                continue
            except (SystemExit, KeyboardInterrupt):
                # an exceptional exception to the "yield exceptions" exception
                # for these exit exceptions
                raise
            except BaseException as exc:
                if not yield_exceptions:
                    raise
                yield i, exc

            # we create the next next task next:
            itr = itrs[i]
            task = asyncio.create_task(itr.__anext__())
            name = task.get_name()
            tasks[name] = i, task


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
    coro: Coroutine[Any, Any, _T],
    *,
    name: str | None = None
) -> asyncio.Task[_T]:
    if isinstance(coro, asyncio.Task):
        if name and not coro.done() and coro.get_name().startswith('Task-'):
            # only replace name if not done and it has no custom name
            coro.set_name(name)
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

    loop = get_event_loop()

    def _fn(
        fut: asyncio.Future[_R],
        args: _P.args,
        kwargs: _P.kwargs
    ) -> None:
        try:
            res = fn(*args, **kwargs)
        except Exception as e:
            fut.set_exception(e)
        else:
            fut.set_result(res)

    @functools.wraps(fn)
    async def _afn(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        fut = loop.create_future()
        loop.call_soon(_fn, fut, args, kwargs)
        return await fut

    return _afn


class CoroWrapper(DefaultCoroutine[_T_co], Generic[_T_co]):
    __slots__ = ('__wrapped__', )

    __wrapped__: _T_co

    def __init__(self, value: _T_co):
        self.__wrapped__ = value

    def __await__(self) -> Generator[Any, None, _T_co]:
        yield
        return self.__wrapped__
