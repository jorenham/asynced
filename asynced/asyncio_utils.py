from __future__ import annotations

import functools
import inspect

__all__ = (
    'amap_iter',
    'race',
    'resume',

    'get_event_loop',
    'call_soon',
    'call_soon_task',
    'create_future',
    'create_task',
)

import asyncio

from typing import (
    Any,
    AsyncIterable,
    AsyncIterator,
    Awaitable,
    Callable,
    cast,
    Coroutine,
    Literal,
    overload,
    TypeVar,
)
from typing_extensions import ParamSpec

from asynced._typing import Maybe, Nothing, NothingType
from asynced.compat import anext, aiter

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


def amap_iter(
    function: Callable[[_T], _R] | Callable[[_T], Awaitable[_R]],
    iterable: AsyncIterable[_T],
) -> AsyncIterable[_R]:
    async def _iterable() -> AsyncIterable[_R]:
        is_async = asyncio.iscoroutinefunction(function) or None

        async for value in iterable:
            res = function(value)

            if is_async is None:
                is_async = inspect.isawaitable(res)

            if is_async:
                yield await cast(Awaitable[_R], res)
            else:
                yield cast(_R, res)

    return _iterable()


@overload
def race(
    *args: AsyncIterable[_T],
    yield_exceptions: Literal[True],
) -> AsyncIterator[tuple[int, _T | BaseException]]:
    ...


@overload
def race(
    *args: AsyncIterable[_T],
    yield_exceptions: Literal[False] = ...,
) -> AsyncIterator[tuple[int, _T]]:
    ...


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
        task = asyncio.create_task(
            cast(Coroutine[Any, None, _T], itr.__anext__())
        )
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
            task = asyncio.create_task(
                cast(Coroutine[Any, None, _T], itr.__anext__())
            )
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


def call_soon(
    callback: Callable[_P, Any],
    *args: _P.args,
    **kwargs: _P.kwargs
) -> asyncio.Handle:
    """Like asyncio.get_event_loop().call_soon(), but accepts keyword args."""
    return get_event_loop().call_soon(
        functools.partial(callback, *args, **kwargs)
    )


def call_soon_task(
    callback: Callable[_P, _R],
    *args: _P.args,
    **kwargs: _P.kwargs
) -> asyncio.Task[_R]:
    """Like asyncio.get_event_loop().call_soon(), but returns an asyncio.Task
    instead of asyncio.Handle
    """
    loop = get_event_loop()
    future = loop.create_future()

    def callback_wrapper():
        try:
            result = callback(*args, **kwargs)
            future.set_result(result)
        except asyncio.CancelledError:
            future.cancel()
            handle.cancel()
            raise
        except BaseException as exc:
            future.set_exception(exc)
            raise

        return result

    async def handle_watcher():
        while not handle.cancelled():
            if future.done():
                return future.result()

            try:
                return await asyncio.wait_for(asyncio.shield(future), 0.1)
            except asyncio.TimeoutError:
                await asyncio.sleep(0)

        assert handle.cancelled()
        raise asyncio.CancelledError()

    handle = loop.call_soon(callback_wrapper)
    return asyncio.create_task(handle_watcher())


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
