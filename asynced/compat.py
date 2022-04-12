from __future__ import annotations

__all__ = ('aiter', 'anext')

import sys

from typing import (
    AsyncIterable,
    AsyncIterator,
    Awaitable,
    Callable,
    cast,
    overload,
    TypeVar,
)

from ._typing import Maybe, Nothing

_T = TypeVar('_T')
_T1 = TypeVar('_T1')
_T2 = TypeVar('_T2')


def py_aiter(iterable: AsyncIterable[_T]) -> AsyncIterator[_T]:
    """Pure-python backport of the builtin aiter() from 3.10."""
    try:
        __aiter__ = iterable.__aiter__
    except AttributeError:
        raise TypeError(f'{iterable!r} is not an async iterable')

    return __aiter__()


@overload
async def py_anext(__i: AsyncIterator[_T]) -> _T: ...
@overload
async def py_anext(__i: AsyncIterator[_T1], default: _T2) -> _T1 | _T2: ...


def py_anext(
    iterator: AsyncIterator[_T1],
    default: Maybe[_T2] = Nothing,
) -> Awaitable[_T1 | _T2]:
    """Pure-python backport of the builtin anext() from 3.10.

    Based on py_anext from
    https://github.com/python/cpython/blob/3.10/Lib/test/test_asyncgen.py
    """
    try:
        __anext__: Callable[[AsyncIterator[_T1]], Awaitable[_T1]]
        __anext__ = type(iterator).__anext__
    except AttributeError:
        raise TypeError(f'{iterator!r} is not an async iterator')

    if default is Nothing:
        return __anext__(iterator)

    async def anext_impl() -> _T1 | _T2:
        try:
            return await __anext__(iterator)
        except StopAsyncIteration:
            return cast(_T2, default)

    return anext_impl()


if sys.version_info >= (3, 10):
    from builtins import anext, aiter
else:
    # anext() and aiter() were only released in 3.10, while async iterators
    # have existed since 3.5. That's a gap of over 6 years FFS!
    #
    # https://peps.python.org/pep-0492/
    # https://docs.python.org/3.10/library/functions.html#anext

    # noinspection PyShadowingBuiltins
    aiter, anext = py_aiter, py_anext
