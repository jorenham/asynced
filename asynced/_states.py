from __future__ import annotations

__all__ = ('SimpleStateBase', 'SimpleStateValue')

import abc
import asyncio
import functools
import inspect
from typing import (
    Any,
    Awaitable,
    Callable,
    Coroutine,
    Generator,
    Generic,
    Hashable,
    TypeVar,
    Union,
)

from typing_extensions import ParamSpec, TypeAlias

from ._typing import Maybe, Nothing
from .asyncio_utils import get_event_loop
from .exceptions import StateError


_T = TypeVar('_T')
_VT = TypeVar('_VT')
_RT = TypeVar('_RT')

_S = TypeVar('_S', bound=Hashable)

_P = ParamSpec('_P')


_FutureOrCoro: TypeAlias = Union[asyncio.Future[_T], Coroutine[Any, Any, _T]]
_MaybeAsync: TypeAlias = Union[Callable[_P, _T], Callable[_P, Awaitable[_T]]]


class FutureWrapperMixin(Generic[_S]):
    __slots__ = ()

    @abc.abstractmethod
    def as_future(self) -> asyncio.Future[_S]:
        ...


class SimpleStateBase(Awaitable[_S], FutureWrapperMixin[_S], Generic[_S]):
    __slots__ = ()

    @abc.abstractmethod
    def as_future(self) -> asyncio.Future[_S]:
        ...

    def __del__(self):
        try:
            self.as_future().cancel()
        except RuntimeError:
            pass

    def __await__(self) -> Generator[Any, None, _S]:
        return self.as_future().__await__()

    def __bool__(self) -> bool:
        return bool(self._raw_value)

    def __repr__(self) -> str:
        return f'<{type(self).__name__}{self._format()} at {id(self):#x}>'

    def __str__(self) -> str:
        return f'<{type(self).__name__}{self._format()}>'

    @property
    def readonly(self) -> bool:
        """Returns true if the state value has a coro or task that will set the
        value in the future.
        """
        return isinstance(self.as_future(), asyncio.Task)

    # State information

    def _update(self, future: asyncio.Future[_S]) -> None:
        ...

    # Internal methods

    @property
    def _raw_value(self) -> _S | BaseException | None:
        """For pattern matching."""
        fut = self.as_future()
        if not fut.done():
            return None
        try:
            return fut.result()
        except BaseException as exc:
            return exc

    def _format(self) -> str:
        fut = self.as_future()

        if fut.done():
            return f'({self._raw_value!r})'

        return f'({fut!r})'


class SimpleStateValue(SimpleStateBase[_S], Generic[_S]):
    __slots__ = ('_future', )

    _future: asyncio.Future[_S]

    def __init__(self, coro: Maybe[_FutureOrCoro[_S]] = Nothing) -> None:
        if coro is Nothing:
            self._future = get_event_loop().create_future()
        elif isinstance(coro, asyncio.Future):
            self._future = coro
        elif inspect.isawaitable(coro):
            self._future = asyncio.create_task(coro)
        else:
            raise TypeError(f'a future or coroutine was expected, got {coro!r}')

        if isinstance(self._future, asyncio.Task):
            self._future.add_done_callback(self._update)

    def as_future(self) -> asyncio.Future[_S]:
        return self._future

    def set(self, state: _S):
        fut = self.as_future()

        if fut.done():
            # ensure any set exceptions are raised if, or if cancelled,
            # asyncio.CancelledError is raised
            _state = fut.result()

            # otherwise, it's already set, which can only be done once
            raise StateError(f'state is already set: {_state.result()!r}')

        elif isinstance(fut, asyncio.Task):
            raise StateError('state is readonly')

        fut.set_result(state)
        self._update(fut)

