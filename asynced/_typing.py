from __future__ import annotations

__all__ = (
    'AnyCoro',
    'MaybeCoro',
    'Raisable',

    'Func0',
    'Func1',
    'Func2',

    'AFunc0',
    'AFunc1',
    'AFunc2',

    'DefaultCoroutine',

    'Maybe',
    'Nothing',
    'NothingType',

    'awaitable',
    'acallable',
)

import abc
import asyncio
import enum
from types import TracebackType
from typing import (
    Any,
    Awaitable,
    Callable,
    cast, Coroutine,
    final,
    Final,
    Generator,
    Generic,
    Literal,
    NoReturn,
    Protocol,
    runtime_checkable,
    TypeVar,
    Union,
)

from typing_extensions import TypeAlias, TypeGuard

_T = TypeVar('_T')
_T_co = TypeVar('_T_co', covariant=True)
_T_contra = TypeVar('_T_contra', contravariant=True)

_R = TypeVar('_R')
_A0 = TypeVar('_A0')
_A1 = TypeVar('_A1')

# Various type aliases

AnyCoro: TypeAlias = Union[Awaitable[_T], Coroutine[Any, Any, _T]]
MaybeCoro: TypeAlias = Union[AnyCoro[_T], _T]

Raisable: TypeAlias = Union[type[BaseException], BaseException]

Func0: TypeAlias = Callable[[], _R]
Func1: TypeAlias = Callable[[_A0], _R]
Func2: TypeAlias = Callable[[_A0, _A1], _R]

AFunc0: TypeAlias = Union[
    Func0[Awaitable[_R]],
    Func0[Coroutine[Any, Any, _R]]
]
AFunc1: TypeAlias = Union[
    Func1[_A0, Awaitable[_R]],
    Func1[_A0, Coroutine[Any, Any, _R]]
]
AFunc2: TypeAlias = Union[
    Func2[_A0, _A1, Awaitable[_R]],
    Func2[_A0, _A1, Coroutine[Any, Any, _R]]
]


# Abstract base classes

class DefaultCoroutine(Coroutine[NoReturn, NoReturn, _T_co], Generic[_T_co]):
    """Abstract base class that passes asyncio.iscoroutine."""

    @abc.abstractmethod
    def __await__(self) -> Generator[Any, None, _T_co]: ...

    def send(self, __value: None) -> NoReturn:
        raise StopIteration

    # noinspection PyMethodMayBeStatic
    def throw(
        self,
        __typ: type[BaseException] | BaseException,
        __val: BaseException | object | None = None,
        __tb: TracebackType | None = None
    ) -> NoReturn:
        if __val is None:
            if __tb is None:
                raise __typ
            raise cast(type[BaseException], __typ)()
        if __tb is not None:
            raise __val.with_traceback(__tb)  # type: ignore
        assert False

    def close(self) -> NoReturn:
        raise StopIteration


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
