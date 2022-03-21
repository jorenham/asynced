from __future__ import annotations

import asyncio
import enum
import sys
from typing import (
    Any,
    Awaitable,
    Callable,
    Coroutine,
    final,
    Final,
    Literal,
    Protocol,
    runtime_checkable,
    TypeVar,
    Union,
)
if sys.version_info < (3, 10):
    from typing_extensions import TypeAlias, TypeGuard
else:
    from typing import TypeAlias, TypeGuard

_T = TypeVar('_T')
_T_co = TypeVar('_T_co', covariant=True)
_T_contra = TypeVar('_T_contra', contravariant=True)


# Various type aliases

AnyCoro: TypeAlias = Union[Awaitable[_T], Coroutine[Any, Any, _T]]

MaybeCoro: TypeAlias = Union[Awaitable[_T], Coroutine[Any, Any, _T], _T]

Raisable: TypeAlias = Union[type[BaseException], BaseException]


# Interfaces

@runtime_checkable
class SupportsAnext(Protocol[_T_co]):
    def __anext__(self) -> Awaitable[_T_co]: ...


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


def awaitable(arg: object) -> TypeGuard[Awaitable[Any]]:
    """Type guard objects that can be used in an 'await ...' expression."""
    if hasattr(arg, '__await__'):
        return True

    return asyncio.iscoroutine(arg)


def acallable(arg: object) -> TypeGuard[
    Callable[..., Coroutine[Any, Any, Any]]
]:
    """Type guard for coroutine (async) functions"""
    return callable(arg) and asyncio.iscoroutinefunction(arg)
