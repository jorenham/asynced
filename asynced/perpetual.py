from __future__ import annotations

__all__ = ('Perpetual', 'ensure_perpetual')

import asyncio
import contextvars
import logging
import reprlib
from typing import (
    Any,
    AsyncGenerator,
    AsyncIterable,
    AsyncIterator,
    Awaitable,
    Callable,
    cast, Final,
    Generator,
    Generic,
    Literal,
    TypeVar,
)

from . import asyncio_utils

from ._compat import anext, aiter
from ._typing import TypeAlias

_T = TypeVar('_T')
_E = TypeVar('_E', bound=BaseException)
_S = TypeVar('_S', bound='Perpetual')

_Callback: TypeAlias = Callable[[_T], Any]
_Callbacks: TypeAlias = list[tuple[_Callback[_T], contextvars.Context]]


logger: Final[logging.Logger] = logging.getLogger('asynced.perpetual')


class Perpetual(Awaitable[_T], AsyncIterator[_T], Generic[_T]):
    """Where asyncio futures are the bridge between low-level events and a
    coroutines, perpetuals are the bridge between a event streams and async
    iterators.

    In it's essence, a perpetual is an asyncio.Future that can have its result
    (or exception) set multiple times, at least until it is cancelled. Besides
    a perpetual being awaitable just like a future, it is an async iterator as
    well.

    The perpetual can be at one of three result states at a time:
        Empty:  Neither result / exception are available.
        Result: A result is available.
        Error:  An exception is available.

    Independent from the result state, the perpetual is either:
        Pending:    A result / exception can be set.
        Done:       No result / exception can be set.

    These states can easily be determined using pattern matching, e.g.
    ``match Perpetual(None, False)`` matches if empty and pending,
    ``match Perpetual(42, True)`` matches if the result is 42 and stopped, and
    ``match Perpetual(BaseException)`` matches if any exception is set.

    A perpetual has an interface that mostly mimicks that of asyncio.Future,
    with a couple of key differences:

    - set_result() and set_exception() can be called more than ones to
      overwrite the current state.

    - done() is True only when stopped, and cancel() only applies to empty
      perpetuals.

    - The methods {add,remove}_callback() replace
      asyncio.Future.{add,remove}_done_callback(), as the callback functions
      are called each time a result is set.

    """
    __slots__ = (
        '_loop',
        '_callbacks_result',
        '_callbacks_exception',
        '_callbacks_done',
        '_present',
        '_future'
    )
    __match_args__: Final = ('raw_result', 'is_done')

    _loop: Final[asyncio.AbstractEventLoop]

    _callbacks_result: _Callbacks[_T]
    _callbacks_exception: _Callbacks[BaseException]
    _callbacks_done: _Callbacks[Perpetual[_T]]

    _present: asyncio.Future[_T]
    _future: asyncio.Future[_T]

    def __init__(
        self,
        *,
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        """Initialize the future.

        The optional event_loop argument allows explicitly setting the event
        loop object used by the perpetual. If it's not provided, the perpetual
        uses the default event loop.
        """
        self._loop = loop or asyncio_utils.ensure_event_loop()
        self._callbacks_result = []
        self._callbacks_exception = []
        self._callbacks_done = []

        self._present = self._future = self._loop.create_future()
        self._future.add_done_callback(self.__schedule_callbacks)

    def __repr__(self) -> str:
        info = [self._state.lower()]
        if self._exception is not None:
            info.append(f'exception={self._exception!r}')
        else:
            result = '...' if self.empty() else reprlib.repr(self.result())
            info.append(f'{result=}')

        return f'<{self.__class__.__name__} {" ".join(info)}>'

    def __await__(self) -> Generator[Any, None, _T]:
        return self._present.__await__()

    def __aiter__(self: _S) -> _S:
        return self

    async def __anext__(self) -> _T:
        try:
            return await self._future
        except asyncio.CancelledError as e:
            raise StopAsyncIteration from e

    @property
    def raw_result(self) -> _T | BaseException | None:
        """Directly returns rhe result, exception, or or None if empty."""
        present = self._present
        if not present.done() or present.cancelled():
            return None
        if (exc := present.exception()) is not None:
            return exc
        return present.result()

    # For compatibility with asyncio.Future

    @property
    def _exception(self) -> BaseException | None:
        present = self._present
        if present.done() and not present.cancelled():
            return present.exception()
        return None

    @property
    def _result(self) -> _T | None:
        present = self._present
        if present.done() and not (present.cancelled() or present.exception()):
            return present.result()
        return None

    @property
    def _state(self) -> Literal['EMPTY', 'CANCELLED', 'PENDING', 'DONE']:
        if not self._present.done():
            return 'EMPTY'
        if self._present.cancelled():
            return 'CANCELLED'
        if self._future.cancelled():
            return 'DONE'
        return 'PENDING'

    @property
    def _log_traceback(self) -> bool:
        return False

    # Public API methods

    def get_loop(self) -> asyncio.AbstractEventLoop:
        return self._future.get_loop()

    def cancel(self, msg: str | None = None) -> bool:
        """Cancel the perpetual and schedule callbacks.

        If the perpetual is not empty or is already cancelled, return False.
        Otherwise, change the perpetual's state to cancelled, schedule the
        callbacks, and return True.
        """
        cancelled = self._present.cancel(msg)

        # present got cancelled iff. present was not done => present == future
        assert not cancelled or self._future.cancelled()

        return cancelled

    def cancelled(self) -> bool:
        """Return True if the perpetual was cancelled."""
        return self._present.cancelled()

    def stop(self) -> bool:
        """Stop the perpetual.

        If the perpetual is empty, cancels instead. Otherwise, change the
        perpetual's state to stopped, and return True.
        """
        stopped = self._future.cancel()

        # present must be either cancelled, result or error
        assert self._present.done()

        return stopped

    def done(self) -> bool:
        """Return True if the perpetual is done.

        Done means that the perpetual was cancelled and a result / exception is
        available.
        """
        return self._future.cancelled()

    def empty(self) -> bool:
        """Return True if the perpetual is Empty.

        Empty means either that a result / exception is not available.
        """
        return not self._present.done() or self._present.cancelled()

    def result(self) -> _T:
        """Return the current result that this perpetual represents.

        If the perpetual is empty, raises asyncio.InvalidStateError. If the
        perpetual has an exception set, this exception is raised.
        """
        return self._present.result()

    def exception(self) -> BaseException | None:
        """Return the exception that was set on this perpetual.

        The exception (or None if no exception was set) is returned only if
        the perpetual is not empty. If the perpetual has been cancelled, raises
        asyncio.CancelledError.  If the perpetual is empty, raises
        asyncio.InvalidStateError.
        """
        return self._present.exception()

    def add_result_callback(
        self,
        fn: Callable[[_T], Any],
        *,
        context: contextvars.Context | None = None,
        immediate: bool = True,
    ) -> None:
        """Add a callback to be run when the perpetual result or exception is
        set.

        The callback is called with a single argument - the perpetual object.
        If the perpetual is not empty when this is called, the callback is
        scheduled with call_soon.

        If immediate is True (the default) and a result is currently available,
        the callback will be scheduled to run immediately
        """
        if self.cancelled():
            raise asyncio.CancelledError

        present = self._present

        if context is None:
            context = contextvars.copy_context()
        self._callbacks_result.append((fn, context))

        if immediate and present.done():
            self.__schedule_callbacks(present)

    def remove_result_callback(self, fn: _Callback) -> int:
        """Remove all instances of a callback from the "call when done" list.

        Returns the number of callbacks removed.
        """
        filtered = [
            (f, ctx)
            for (f, ctx) in self._callbacks_result
            if f != fn
        ]
        if removed_count := len(self._callbacks_result) - len(filtered):
            self._callbacks_result[:] = filtered

        return removed_count

    def set_result(self, result: _T) -> None:
        """Set or update the result of the perpetual and schedule the callbacks.

        If the perpetual is cancelled when this method is called, raises
        asyncio.CancelledError.
        """
        if self._future.cancelled():
            raise asyncio.CancelledError

        self.__rotate()
        self._present.set_result(result)

    def set_exception(self, exception: BaseException) -> None:
        """Set or update the exception of the perpetual.

        If the perpetual is cancelled when this method is called, raises
        asyncio.CancelledError.
        """
        if self._future.cancelled():
            raise asyncio.CancelledError

        self.__rotate()
        self._present.set_exception(exception)

    # So-called internal methods

    def __rotate(self) -> None:
        # rotate the futures; ensuring that the future is ahead of us
        next_future = self._loop.create_future()
        next_future.add_done_callback(self.__schedule_callbacks)

        self._present, self._future = self._future, next_future

    def __schedule_callbacks(self, present: asyncio.Future[_T]) -> None:
        assert present.done()

        arg: Any
        callbacks: _Callbacks[Any]

        if present.cancelled():
            arg = self
            if callbacks := self._callbacks_done[:]:
                self._callbacks_done[:] = []

        elif (exc := present.exception()) is not None:
            arg = exc
            callbacks = self._callbacks_exception[:]

        else:
            arg = present.result()
            callbacks = self._callbacks_result[:]

        for callback, ctx in callbacks:
            self._loop.call_soon(callback, arg, context=ctx)  # type: ignore


def ensure_perpetual(
    iterable: AsyncIterable[_T],
    *,
    loop: asyncio.AbstractEventLoop | None = None
) -> Perpetual[_T]:
    if isinstance(iterable, Perpetual):
        return iterable

    source: AsyncIterator[_T]
    is_agen = False

    if not isinstance(iterable, AsyncIterator):
        source = aiter(iterable)
    else:
        source = iterable
        is_agen = isinstance(iterable, AsyncGenerator)

    def _stop_aiter(raised: bool) -> None:
        if not raised and is_agen:
            # shut down the async generator
            logger.debug(
                'scheduling shutdown of async generator wrapped in %s',
                repr(sink)
            )
            sink.get_loop().create_task(
                cast(AsyncGenerator[_T, Any], source).aclose()
            )

        # propagate cancellation to the perpetual
        if not sink.cancelled():
            logger.debug(
                'cancelling perpatual after stop iteration in %s ',
                repr(sink)
            )
            sink.cancel()

    async def _call_anext() -> _T:
        return await asyncio.shield(anext(source))

    def _schedule_anext() -> None:
        if not sink.done() and not sink.cancelled():
            result = sink.get_loop().create_task(_call_anext())
            result.add_done_callback(_call_set_result)
        else:
            logger.debug('not scheduling the anext() for %s', repr(sink))

    def _call_set_result(result: asyncio.Task[_T]) -> None:
        assert result.done()

        must_cancel = False
        raised = False

        if sink.cancelled():
            logger.debug('%s got cancelled; stopping its iterator')
            must_cancel = True

        if result.cancelled():
            logger.debug('ensure_perpetual __anext__ task was cancelled')
            must_cancel = True

        else:
            try:
                value = result.result()
            except (StopAsyncIteration, StopIteration) as e:
                logger.debug(
                    'cancelling %s after caught %s in wrapped iterator',
                    repr(sink), str(e)
                )
                must_cancel = True
            except asyncio.CancelledError as e:
                logger.warning(
                    'bare %s caught in wrapped iterator %s',
                    str(e), repr(sink)
                )
                must_cancel = True
            except (KeyboardInterrupt, SystemExit) as e:
                logger.debug(
                    '%s caught in wrapped iterator %s',
                    str(e), repr(sink)
                )
                if not sink.cancelled():
                    sink.set_exception(e)

                raise

            except BaseException as e:
                logger.debug(
                    '%s caught in wrapped iterator %s',
                    str(e), repr(sink)
                )
                if sink.cancelled():
                    logger.warning(
                        'error %s from wrapped iterator was never raised in %s',
                        repr(e), repr(sink)
                    )
                else:
                    sink.set_exception(e)

                raised = True

            else:
                if sink.cancelled() or must_cancel:
                    logger.warning(
                        'discarding iterator value %s in %s',
                        repr(value), repr(sink)
                    )
                    must_cancel = True
                else:
                    sink.set_result(value)

        if must_cancel:
            _stop_aiter(raised)
        else:
            _schedule_anext()

    sink: Perpetual[_T] = Perpetual(loop=loop)
    sink.get_loop().call_soon(_schedule_anext)
    return sink
