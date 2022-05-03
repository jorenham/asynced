from __future__ import annotations

__all__ = (
    'ItemCollection',
    'Comparable',

    'EllipsisType',

    'OneOrMany',
    'ObjOrType',

    'Catchable',
    'CatchableE',
    'Throwable',
    'ThrowableE',

    'Maybe',
    'Nothing',
    'NothingType',

    'awaitable',
    'acallable',
    'ishashclass',
)

import asyncio
import enum
import inspect

from typing import (
    Any,
    Awaitable,
    Callable,
    Coroutine,
    final,
    Final,
    get_args,
    get_origin,
    Hashable,
    Iterator,
    Literal,
    overload,
    Protocol,
    TypeVar,
    Union,
)

from typing_extensions import ParamSpec, Self, TypeAlias, TypeGuard

_T = TypeVar('_T')
_T_co = TypeVar('_T_co', covariant=True)
_T_contra = TypeVar('_T_contra', contravariant=True)

_VT = TypeVar('_VT')
_VT_co = TypeVar('_VT_co', covariant=True)
_VT_contra = TypeVar('_VT_contra', contravariant=True)

_KT = TypeVar('_KT')
_KT_co = TypeVar('_KT_co', covariant=True)
_KT_contra = TypeVar('_KT_contra', contravariant=True)

_OT = TypeVar('_OT', bound=object)
_ET = TypeVar('_ET', bound=BaseException)

_P = ParamSpec('_P')
_P0 = TypeVar('_P0')
_P1 = TypeVar('_P1')

_R = TypeVar('_R')

# Various type aliases

EllipsisType = type(Ellipsis)

OneOrMany: TypeAlias = Union[_T, tuple[_T, ...]]
ObjOrType: TypeAlias = Union[_OT, type[_OT]]

Catchable: TypeAlias = OneOrMany[type[_ET]]
CatchableE: TypeAlias = Catchable[Exception]

Throwable: TypeAlias = ObjOrType[_ET]
ThrowableE: TypeAlias = Throwable[Exception]


class Comparable(Protocol):
    def __eq__(self, __o: object, /) -> bool: ...


class ItemCollection(Protocol[_KT_contra, _T_co]):
    __slots__ = ()

    def __len__(self) -> int: ...

    @overload
    def __getitem__(self, __k: _KT_contra) -> _T_co: ...
    @overload
    def __getitem__(self, __ks: slice) -> Self: ...

    def __iter__(self) -> Iterator[_T_co]: ...

# Sentinel for missing values, distinguishable from None


@final
class _NothingEnum(enum.Enum):
    NOTHING = object()

    def __repr__(self) -> str:
        return self._name_.title()

    def __str__(self) -> str:
        return '∅'

    def __bool__(self) -> bool:
        return False

    def __eq__(self, other: object) -> TypeGuard[NothingType]:
        return other is self._value_

    def __hash__(self) -> int:
        return hash(self._value_)


NothingType: TypeAlias = Literal[_NothingEnum.NOTHING]
Nothing: Final[NothingType] = _NothingEnum.NOTHING
Maybe: TypeAlias = Union[_T, NothingType]


# Type guards


def awaitable(arg: _T) -> TypeGuard[Awaitable[_T]]:
    """Type guard objects that can be used in an 'await ...' expression."""
    if isinstance(arg, type):
        return False

    if asyncio.isfuture(arg):
        return True

    if callable(getattr(arg, '__await__', None)):
        return True

    return inspect.isawaitable(arg)


def acallable(arg: Any) -> TypeGuard[Callable[..., Coroutine[Any, Any, Any]]]:
    """Type guard for coroutine (async) functions"""
    return callable(arg) and asyncio.iscoroutinefunction(arg)


def ishashclass(cls: type[Any]) -> TypeGuard[Hashable]:
    """Return True if the class and its generic type args are hashable."""
    try:
        return issubclass(cls, Hashable)
    except TypeError:
        if (cls_orig := get_origin(cls)) is None:
            raise

        if not issubclass(cls_orig, Hashable):
            return False

        # recurse on the generic type args
        return all(map(ishashclass, get_args(cls)))
