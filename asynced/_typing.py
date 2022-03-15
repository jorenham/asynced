from __future__ import annotations

import abc
import asyncio
import enum
import sys
from types import TracebackType
from typing import (
    Any,
    Awaitable,
    Callable,
    ClassVar,
    ContextManager,
    Coroutine,
    final,
    Final,
    Literal,
    overload,
    Protocol,
    runtime_checkable,
    Type,
    TypeVar,
    Hashable,
    Union,
)
if sys.version_info < (3, 10):
    from typing_extensions import (
        Concatenate,
        ParamSpec,
        TypeAlias,
        TypeGuard,
    )
else:
    from typing import (
        Concatenate,
        ParamSpec,
        TypeAlias,
        TypeGuard,
    )

# Any type
_T = TypeVar('_T')
_T_co = TypeVar('_T_co', covariant=True)
_T_contra = TypeVar('_T_contra', contravariant=True)

_AT_contra = TypeVar('_AT_contra', contravariant=True)  # argument type

# Unbound and Bound descriptor return type
_BT_co = TypeVar('_BT_co', covariant=True)
_UT_co = TypeVar('_UT_co', covariant=True)

# value type
_VT = TypeVar('_VT', bound=Hashable)
_VT_co = TypeVar('_VT_co', bound=Hashable, covariant=True)

# exception type
_ET = TypeVar('_ET', bound=BaseException)  # exception type

# Various type aliases

LiteralType: TypeAlias = Union[None, bool, int, bytes, str, enum.Enum]

AnyFuture: TypeAlias = Union[asyncio.Future[_T], Awaitable[_T]]

Identity: TypeAlias = Callable[[_T], _T]


# Type guard functions

def is_callable(obj: object) -> TypeGuard[Callable[..., Any]]:
    return callable(obj)


def is_async_callable(
    obj: object,
) -> TypeGuard[Callable[..., Coroutine[Any, Any, Any]]]:
    if not is_callable(obj):
        return False
    return asyncio.iscoroutinefunction(obj)

# Interfaces


@runtime_checkable
class Lockable(ContextManager[bool], Protocol):
    __slots__ = ()

    def __enter__(self) -> bool:
        return self.acquire()

    def __exit__(
        self,
        exc_type: Type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> Union[bool, None]:
        return self.release()

    def acquire(self, blocking: bool = ...) -> bool: ...

    def release(self) -> bool | None: ...


# Descriptor protocols.

@runtime_checkable
class SupportsSetName(Protocol[_T_contra]):
    def __set_name__(self, owner: Type[_T_contra], name: str) -> None: ...


# noinspection PyOverloads
@runtime_checkable
class SupportsGet(Protocol[_T_contra, _UT_co, _BT_co]):
    @overload
    def __get__(
        self, __obj: None, __cls: Type[_T_contra] | None = ...
    ) -> _UT_co: ...

    @overload
    def __get__(
        self, __obj: _T_contra, __cls: Type[_T_contra] | None = ...
    ) -> _BT_co: ...


@runtime_checkable
class SupportsSet(Protocol[_T_contra, _AT_contra]):
    def __set__(self, instance: _T_contra, value: _AT_contra) -> None: ...


@runtime_checkable
class SupportsDelete(Protocol[_T_contra]):
    def __delete__(self, instance: _T_contra) -> None: ...


# The "supports" protocols that for some weird reason aren't in typing

@runtime_checkable
class SupportsBool(Protocol):
    @abc.abstractmethod
    def __bool__(self) -> bool: ...


@runtime_checkable
class SupportsAnext(Protocol[_T_co]):
    def __anext__(self) -> Awaitable[_T_co]: ...


# Analogue to None, for missing values


@final
class _NothingEnum(enum.Enum):
    __sentinel__: ClassVar[bool] = True

    NOTHING = object()

    def __repr__(self) -> str:
        return self._name_.title()

    def __str__(self) -> str:
        return '∅'

    def __bool__(self) -> bool:
        return False

    def __int__(self) -> int:
        return 0


NothingType: TypeAlias = Literal[_NothingEnum.NOTHING]
Nothing: Final[NothingType] = _NothingEnum.NOTHING

Maybe: TypeAlias = Union[_T, NothingType]
