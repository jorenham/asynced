__all__ = (
    'PY310', 'PY311',
    'anext', 'aiter',
)

from typing import Final
import sys

PY310: Final[bool] = sys.version_info >= (3, 10)
PY311: Final[bool] = sys.version_info >= (3, 11)

if sys.version_info < (3, 10):
    """anext() and aiter() were only released in 3.10, while async iterators 
    have existed since 3.5. That's a gap of over 6 years FFS!

    https://peps.python.org/pep-0492/
    https://docs.python.org/3.10/library/functions.html#anext
    """
    from typing import (
        AsyncIterable,
        AsyncIterator,
        Final,
        Literal,
        overload,
        TypeVar,
        Union,
    )
    from ._typing import SupportsAnext, TypeAlias

    _T = TypeVar('_T')
    _VT = TypeVar('_VT')

    MissingT: TypeAlias = Literal['__missing__']
    MISSING: Final[MissingT] = '__missing__'

    # noinspection PyShadowingBuiltins
    def aiter(iterable: AsyncIterable[_T]) -> AsyncIterator[_T]:
        if not hasattr(iterable, '__aiter__'):
            raise TypeError(
                f'{iterable.__class__.__name__!r} object is not an async '
                f'iterable'
            ) from None

        return iterable.__aiter__()

    # noinspection PyShadowingBuiltins
    @overload
    async def anext(iterable: SupportsAnext[_T]) -> _T: ...

    # noinspection PyShadowingBuiltins
    @overload
    async def anext(iterable: SupportsAnext[_T], default: _VT) -> _T: ...

    # noinspection PyShadowingBuiltins
    async def anext(
        iterable: SupportsAnext[_T],
        default: Union[_VT, MissingT] = MISSING
    ) -> Union[_T, _VT]:
        if not hasattr(iterable, '__anext__'):
            raise TypeError(
                f'{iterable.__class__.__name__!r} object is not an async '
                f'iterator'
            ) from None

        try:
            return await iterable.__anext__()
        except StopAsyncIteration:
            if default != MISSING:
                return default
            raise
else:
    from builtins import anext, aiter
