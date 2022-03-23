from __future__ import annotations

__all__ = (
    'PY310',
    'PY311',

    'aiter',
    'anext',
)

from typing import (
    Any,
    AsyncIterable,
    AsyncIterator,
    Awaitable,
    Callable, cast, Coroutine,
    Final,
    Final,
    overload,
    TypeVar,
)
import sys
from ._typing import Maybe, Nothing


_T = TypeVar('_T')
_VT = TypeVar('_VT')


PY310: Final[bool] = sys.version_info >= (3, 10)
PY311: Final[bool] = sys.version_info >= (3, 11)


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
async def py_anext(__i: AsyncIterator[_T], default: _VT) -> _T | _VT: ...


def py_anext(
    iterator: AsyncIterator[_T],
    default: Maybe[_VT] = Nothing,
) -> Awaitable[_T | _VT]:
    """Pure-python backport of the builtin anext() from 3.10.

    Based on py_anext from
    https://github.com/python/cpython/blob/3.10/Lib/test/test_asyncgen.py
    """
    try:
        __anext__: Callable[[AsyncIterator[_T]], Awaitable[_T]] = type(iterator).__anext__
    except AttributeError:
        raise TypeError(f'{iterator!r} is not an async iterator')

    if default is Nothing:
        return __anext__(iterator)

    async def anext_impl() -> _T | _VT:
        try:
            return await __anext__(iterator)
        except StopAsyncIteration:
            return cast(_VT, default)

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
