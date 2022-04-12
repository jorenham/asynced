from __future__ import annotations

__all__ = ('SimpleStateBase', 'SimpleStateValue')

import abc
import asyncio
import inspect
import itertools
from typing import (
    Any,
    Awaitable,
    Callable,
    cast, ClassVar, Coroutine,
    Generator,
    Generic,
    Hashable,
    TypeVar,
    Union,
)

from typing_extensions import ParamSpec, TypeAlias

from ._typing import Maybe, Nothing
from .exceptions import StateError


_T = TypeVar('_T')
_VT = TypeVar('_VT')
_RT = TypeVar('_RT')

_S = TypeVar('_S', bound=Hashable)

_P = ParamSpec('_P')


_FutureOrCoro: TypeAlias = Union[asyncio.Future[_T], Coroutine[Any, Any, _T]]
_MaybeAsync: TypeAlias = Union[Callable[_P, _T], Callable[_P, Awaitable[_T]]]


class SimpleStateBase(Generic[_S]):
    __slots__ = ()
    __match_args__ = ('_raw_value', )

    def __del__(self):
        try:
            self.future.cancel()
        except (AttributeError, RuntimeError):
            pass

    def __await__(self) -> Generator[Any, None, _S]:
        return self.future.__await__()

    def __repr__(self) -> str:
        return f'<{type(self).__name__}{self._format()} at {id(self):#x}>'

    def __str__(self) -> str:
        return f'<{type(self).__name__}{self._format()}>'

    @property
    @abc.abstractmethod
    def future(self) -> asyncio.Future[_S]:
        ...

    @property
    def readonly(self) -> bool:
        """Returns true if an asyncio.Task will set the result, otherwise, the
        asyncio.Future can be set manually.
        """
        return isinstance(self.future, asyncio.Task)

    @property
    def _is_done(self) -> bool:
        return self.future.done()

    @property
    def _is_set(self) -> bool:
        future = self.future
        if not future.done():
            return False
        if future.cancelled():
            return False
        return future.exception() is None

    @property
    def _is_raised(self) -> bool:
        future = self.future
        if not future.done():
            return False
        if future.cancelled():
            return False
        if (exc := future.exception()) is None:
            return False

        return not isinstance(exc, asyncio.CancelledError)

    @property
    def _is_cancelled(self) -> bool:
        future = self.future
        if not future.done():
            return False
        if future.cancelled():
            return True
        return isinstance(future.exception(), asyncio.CancelledError)

    @property
    def _raw_value(self) -> _S | BaseException | None:
        """For pattern matching."""
        fut = self.future
        if not fut.done():
            return None
        try:
            return fut.result()
        except BaseException as exc:
            return exc
    
    def _set(self, value: _S) -> None:
        self.__as_unset_future().set_result(value)
        self._on_set(value)

    def _raise(self, exc: BaseException) -> None:
        if isinstance(exc, asyncio.CancelledError):
            self._cancel()
            return

        self.__as_unset_future().set_exception(exc)
        self._on_error(exc)

    def _cancel(self) -> None:
        self.future.cancel()
        self._on_cancel()

    def _on_set(self, value: Maybe[Hashable] = Nothing) -> None:
        ...

    def _on_error(self, exc: BaseException) -> None:
        ...

    def _on_cancel(self) -> None:
        ...

    def __as_unset_future(self):
        future = self.future
        if self.readonly:
            raise StateError(f'{self!r} is readonly')

        if future.done():
            current = future.result()
            raise StateError(f'{self!r} is already set: {current!r}')

        return future

    def _format(self) -> str:
        fut = self.future

        if fut.done():
            return f'({self._raw_value!r})'

        return ''


class SimpleStateValue(SimpleStateBase[_S], Generic[_S]):
    __slots__ = ('_future', )

    _task_counter: ClassVar[Callable[[], int]] = itertools.count(1).__next__

    _future: asyncio.Future[_S]

    def __init__(
        self,
        coro: Maybe[_FutureOrCoro[_S]] = Nothing,
        *,
        name: str | None = None
    ) -> None:
        loop = asyncio.get_running_loop()

        if coro is Nothing:
            self._future = loop.create_future()
        elif isinstance(coro, asyncio.Future):
            self._future = coro
        elif asyncio.iscoroutine(coro):
            self._future = loop.create_task(coro, name=name)
        else:
            raise TypeError(f'a future or coroutine was expected, got {coro!r}')

        if isinstance(self._future, asyncio.Task):
            self._future.add_done_callback(self._on_task_done)

    def __bool__(self) -> bool:
        return bool(self._raw_value)

    @property
    def future(self) -> asyncio.Future[_S]:
        return self._future

    def set(self, state: _S):
        self._set(state)

    def _on_task_done(self, task: asyncio.Task[_S]):
        assert task.done()

        if task.cancelled():
            self._on_cancel()
        elif (exc := task.exception()) is not None:
            self._on_error(exc)

            if isinstance(exc, (SystemExit, KeyboardInterrupt)):
                raise
        else:
            self._on_set(task.result())
