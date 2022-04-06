from __future__ import annotations

__all__ = (
    'zipped',
    'mapped',
)

import asyncio
import functools
import inspect

from typing import (
    AsyncIterator,
    Awaitable,
    Callable,
    Hashable,
    Protocol,
    TypeVar,
)
from typing_extensions import TypeAlias

from ._typing import Maybe, Nothing
from .asyncio_utils import race
from .states import StateVar as SVar

_T = TypeVar('_T')
_VT = TypeVar('_VT')  # any value type
_RT = TypeVar('_RT')  # any return type

_T1 = TypeVar('_T1')
_T2 = TypeVar('_T2')
_T3 = TypeVar('_T3')

# hashable state types
_S = TypeVar('_S', bound=Hashable)
_S1 = TypeVar('_S1', bound=Hashable)
_S2 = TypeVar('_S2', bound=Hashable)
_S3 = TypeVar('_S3', bound=Hashable)

# callable types
_F0: TypeAlias = Callable[[], _RT]
_F1: TypeAlias = Callable[[_T1], _RT]
_F2: TypeAlias = Callable[[_T1, _T2], _RT]
_F3: TypeAlias = Callable[[_T1, _T2, _T3], _RT]


class _Fn(Protocol[_VT, _RT]):
    __name__: str
    def __call__(self, *args: _VT) -> _RT: ...


def zipped(
    *statevars: SVar[_S],
    default: Maybe[_T] = Nothing
) -> SVar[tuple[_S | _T, ...]]:
    """Combine n state variables into a state variable containing a tuple of
    length n.

    If ``default`` is passed, this will be used for the initially missing
    values. Otherwise, these initial items with missing values will be skipped.

    Consequently, any updates of the statevar will result in an update to the
    zipped one.
    """
    if not len(statevars):
        raise TypeError('zipped() must have at least one argument.')

    async def _producer() -> AsyncIterator[_RT]:
        args = [(await sv) if sv.is_set else default for sv in statevars]
        missing = set(range(len(statevars)))
        async for i, state in race(*statevars):
            args[i] = state

            if i in missing and state is not Nothing:
                missing.remove(i)

            if missing and default is Nothing:
                continue

            yield tuple(args)

    return SVar(_producer())


def gathered(*statevars: SVar[_S]) -> SVar[tuple[_S, ...]]:
    """Like zip, but the returned statevar updates when *all* statevars
    have updated (like asyncio.gather), instead of *any* in zip (like
    asyncio.wait(..., return_when=FIRST_COMPLETED).
    """
    if not len(statevars):
        raise TypeError('zipped() must have at least one argument.')

    async def _producer() -> AsyncIterator[_RT]:

        while True:
            yield tuple(await asyncio.gather(*statevars))

            tasks = [asyncio.create_task(sv.__anext__()) for sv in statevars]

            try:
                await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                args = tuple(
                    task.result() if task.done() else arg
                    for task, arg in zip(tasks, args)
                )

            except (asyncio.CancelledError, StopAsyncIteration):
                break

    return SVar(_producer())


def mapped(
    function: _Fn[Hashable, _RT] | _Fn[Hashable, Awaitable[_RT]],
    *args: SVar[Hashable],
) -> SVar[_RT]:
    """Transform the given function into a function that can be applied on
    state variables as arguments, returning a new state variable.

    The function can be either sync or async, but the resulting function
    will aways be sync.
    """
    if not args:
        raise TypeError('amap() must have at least two arguments')

    afunction: _Fn[_S, Awaitable[_RT]]
    if asyncio.iscoroutinefunction(function):
        afunction = function
    else:
        @functools.wraps(function)
        async def afunction(*_args: _S):
            res = function(*_args)
            return (await res) if inspect.isawaitable(res) else res

    async def _producer():
        async for states in zipped(*args):
            yield await afunction(*states)

    return SVar(_producer())
