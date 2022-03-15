from __future__ import annotations

__all__ = ('MaybeResult', 'Empty', 'Result', 'Error')

import abc
from typing import Any, ClassVar, Final, Generic, NoReturn, TypeVar, Union

from ._typing import TypeAlias

_T = TypeVar('_T')
_RT = TypeVar('_RT')
_ET = TypeVar('_ET', bound=BaseException)


class _SomeResult(Generic[_T]):
    __slots__ = ('value',)

    value: _T

    @property
    @abc.abstractmethod
    def result(self) -> _T | None: ...

    @property
    @abc.abstractmethod
    def exception(self) -> _T | None: ...

    @abc.abstractmethod
    def unwrap(self) -> _T | NoReturn: ...

    def __init__(self, value: _T | _SomeResult[_T]):
        if isinstance(value, _SomeResult):
            self.value = value.value
        else:
            self.value = value

    def __repr__(self) -> str:
        name = self.__class__.__name__
        if self.value is None:
            return name

        return f'{name}({self.value})'

    __str__ = __repr__

    def __setattr__(self, key: str, value: Any) -> None:
        if key == 'value' and hasattr(self, 'value'):
            raise AttributeError(f'{self!r}.value is read-only')

        super().__setattr__(key, value)

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, _SomeResult):
            return type(other) is type(self) and other.value == self.value

        return bool(self.value == other)

    def __hash__(self) -> int:
        return hash(self.value)


class _EmptyType(_SomeResult[None]):
    __slots__ = ()
    __match_args__: ClassVar[tuple[str, ...]] = ()

    @property
    def result(self) -> None:
        return None

    @property
    def exception(self) -> None:
        return None

    def unwrap(self) -> None:
        return None

    def __bool__(self) -> bool:
        return False


Empty: Final[_EmptyType] = _EmptyType(None)


class Result(_SomeResult[_RT]):
    __slots__ = ()
    __match_args__: ClassVar[tuple[str, ...]] = ('value',)

    @property
    def result(self) -> _RT:
        return self.value

    @property
    def exception(self) -> None:
        return None

    def unwrap(self) -> _RT:
        return self.value


class Error(_SomeResult[_ET]):
    __slots__ = ()
    __match_args__: ClassVar[tuple[str, ...]] = ('value',)

    @property
    def result(self) -> None:
        return None

    @property
    def exception(self) -> _ET:
        return self.value

    def unwrap(self) -> NoReturn:
        raise self.value


MaybeResult: TypeAlias = Union[_EmptyType, Result[_RT], Error[_ET]]
