from __future__ import annotations

__all__ = (
    'Thenable',
    'Mappable',
    'Catchable',
)

import abc
from typing import Protocol, runtime_checkable, TypeVar
from typing_extensions import Self

from ._typing import Catchable as CatchableT, XsyncCallable

_T = TypeVar('_T')
_T_co = TypeVar('_T_co', covariant=True)

_R = TypeVar('_R')
_E = TypeVar('_E', bound=BaseException)


@runtime_checkable
class Thenable(Protocol[_T_co]):
    """A generic type that can have its type argument transformed"""
    __slots__ = ()

    @abc.abstractmethod
    def then(self, __func: XsyncCallable[[_T_co], _R], /) -> Self[_R]: ...


@runtime_checkable
class Mappable(Protocol[_T_co]):
    """Like Thenable, but maps to each of the individual elements of the
    generic type."""
    __slots__ = ()

    @abc.abstractmethod
    def map(self, __func: XsyncCallable[[_T_co], _R], /) -> Self[_R]: ...


@runtime_checkable
class Catchable(Protocol[_T_co]):
    """A generic type that can have its exception mapped."""
    __slots__ = ()

    @abc.abstractmethod
    def catch(
        self, __typ: CatchableT[_E], __func: XsyncCallable[[_E], _R], /
    ) -> Self[_T_co | _R]:
        """Create a new instance with the result of the function."""
        ...
