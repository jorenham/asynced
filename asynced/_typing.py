from __future__ import annotations

__all__ = (
    'OneOrMany',
    'ObjOrType',

    'Catchable', 'CatchableE',
    'Throwable', 'ThrowableE',

    'AnyCoro',
    'AsyncCallable',
    'AsyncFunction',
    'AwaitableN',

    'Xsync',
    'XsyncCallable',

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
import inspect
from types import TracebackType
from typing import (
    Any,
    Awaitable,
    Callable,
    cast,
    Coroutine,
    final,
    Final,
    Generator,
    Generic,
    Literal,
    NoReturn,
    Optional,
    TypeVar,
    Union,
)

from typing_extensions import ParamSpec, TypeAlias, TypeGuard

_T = TypeVar('_T')
_T_co = TypeVar('_T_co', covariant=True)
_T_contra = TypeVar('_T_contra', contravariant=True)

_OT = TypeVar('_OT', bound=object)
_ET = TypeVar('_ET', bound=BaseException)

_P = ParamSpec('_P')
_P0 = TypeVar('_P0')
_P1 = TypeVar('_P1')

_R = TypeVar('_R')

# Various type aliases

OneOrMany: TypeAlias = Union[_T, tuple[_T, ...]]
ObjOrType: TypeAlias = Union[_OT, type[_OT]]

Catchable: TypeAlias = OneOrMany[type[_ET]]
CatchableE: TypeAlias = Catchable[type[Exception]]

Throwable: TypeAlias = ObjOrType[_ET]
ThrowableE: TypeAlias = Throwable[Exception]

_AT1: TypeAlias = Awaitable[_T]
_AT2: TypeAlias = _AT1[_AT1[_T]]
_AT3: TypeAlias = _AT2[_AT1[_T]]
_AT4: TypeAlias = _AT3[_AT1[_T]]
_AT5: TypeAlias = _AT4[_AT1[_T]]

AwaitableN: TypeAlias = Union[_AT1[_T], _AT2[_T], _AT3[_T], _AT4[_T], _AT5[_T]]
Xsync: TypeAlias = Union[_T, AwaitableN[_T]]

AnyCoro: TypeAlias = Coroutine[
    Optional[asyncio.Future[Any]],  # .send result is a future when suspending
    None,  # at least true for the python implementation of asyncio.Task
    _T
]
AsyncFunction = Callable[_P, AnyCoro[_R]]
AsyncCallable = Callable[_P, Awaitable[_R]]
XsyncCallable = Callable[_P, Xsync[_T]]


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
    if asyncio.isfuture(arg):
        return True

    if hasattr(arg, '__await__') and callable(arg.__await__):
        return True

    return inspect.isawaitable(arg)


def acallable(arg: object) -> TypeGuard[
    Callable[..., Coroutine[Any, Any, Any]]
]:
    """Type guard for coroutine (async) functions"""
    return callable(arg) and asyncio.iscoroutinefunction(arg)
