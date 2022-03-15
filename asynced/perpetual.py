from __future__ import annotations

__all__ = ('Perpetual', 'ensure_perpetual')

import asyncio
import contextvars
import logging
import reprlib
import sys
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

if sys.version_info < (3, 10):
    from ._compat import anext, aiter
from ._typing import SupportsAnext, TypeAlias

from .result_types import Empty, Result, Error, MaybeResult

_T = TypeVar('_T')
_E = TypeVar('_E', bound=BaseException)
_S = TypeVar('_S', bound='Perpetual')

_Callback: TypeAlias = Callable[[asyncio.Future[_T]], Any]


logger: Final[logging.Logger] = logging.getLogger(__name__)


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
    __slots__ = ('_loop', '_callbacks', '_present', '_future')
    __match_args__: Final = ('maybe_result', 'is_done')

    _loop: Final[asyncio.AbstractEventLoop]

    _callbacks: list[tuple[_Callback[_T], contextvars.Context]]

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
        self._loop = loop or asyncio.get_event_loop()
        self._callbacks = []
        self._present = self._future = self._create_future()

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
    def is_empty(self) -> bool:
        p = self._present
        return not p.done() or p.cancelled()

    @property
    def is_result(self) -> bool:
        p = self._present
        return p.done() and not p.cancelled() and p.exception() is None

    @property
    def is_error(self) -> bool:
        p = self._present
        return p.done() and not p.cancelled() and p.exception() is not None

    @property
    def is_done(self) -> bool:
        return self._future.cancelled()

    @property
    def maybe_result(self) -> MaybeResult[_T, BaseException]:
        """Never raises, but wraps the result / exception.

        Intended for pattern matching: Empty, Result(_), or Error(_).
        """
        present = self._present
        if not present.done() or present.cancelled():
            return Empty
        if (exc := present.exception()) is not None:
            return Error(exc)
        return Result(present.result())

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

    def stop(self, msg: str | None = None) -> bool:
        """Stop the perpetual and schedule callbacks.

        If the perpetual is empty, cancels instead. Otherwise, change the
        perpetual's state to stopped, schedule  the callbacks, and return True.
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
        return not self.is_empty and self._future.cancelled()

    def empty(self) -> bool:
        """Return True if the perpetual is Empty.

        Empty means either that a result / exception is not available.
        """
        return not self._present.done() or self._present.cancelled()

    def result(self) -> _T:
        """Return the current result that this perpetual represents.

        If the perpetual is empty, raises asyncio.InvalidStateError. If the
        perpetual has an exception set, this exception is raised.

        Unline asyncio.Future.result(), Perpetual.result() won't raise
        asyncio.CancelledError if cancelled and a result was set before
        cancelling.
        """
        return self._present.result()

    def exception(self) -> BaseException | None:
        """Return the exception that was set on this perpetual.

        The exception (or None if no exception was set) is returned only if
        the future is not empty. If the perpetual has been cancelled, raises
        asyncio.CancelledError.  If the perpetual is empty, raises
        asyncio.InvalidStateError.
        """
        return self._present.exception()

    def add_result_callback(
        self,
        fn: _Callback[_T],
        *,
        context: contextvars.Context | None = None
    ) -> None:
        """Add a callback to be run when the perpetual result or exception is
        set.

        The callback is called with a single argument - the perpetual object.
        If the perpetual is not empty when this is called, the callback is
        scheduled with call_soon.
        """
        self._future.add_done_callback(fn, context=context)

        if context is None:
            context = contextvars.copy_context()
        self._callbacks.append((fn, context))

        if not self._present.done():
            self._loop.call_soon(fn, self, context=context)  # type: ignore

    def remove_result_callback(self, fn: _Callback) -> int:
        """Remove all instances of a callback from the "call when done" list.

        Returns the number of callbacks removed.
        """
        self._future.remove_done_callback(fn)

        filtered_callbacks = [
            (f, ctx)
            for (f, ctx) in self._callbacks
            if f != fn
        ]
        if removed_count := len(self._callbacks) - len(filtered_callbacks):
            self._callbacks[:] = filtered_callbacks

        return removed_count

    def set_result(self, result: _T) -> None:
        """Set or update the result of the perpetual and schedule the callbacks.

        If the perpetual is cancelled when this method is called, raises
        asyncio.CancelledError.
        """
        if self._future.cancelled():
            raise asyncio.CancelledError

        self._future.set_result(result)

        self.__rotate()

    def set_exception(self, exception: BaseException) -> None:
        """Set or update the exception of the perpetual.

        If the perpetual is cancelled when this method is called, raises
        asyncio.CancelledError.
        """
        if self._future.cancelled():
            raise asyncio.CancelledError

        self._future.set_exception(exception)

        self.__rotate()

    # So-called internal methods

    def _create_future(self) -> asyncio.Future[_T]:
        future = self._loop.create_future()
        for fn, context in self._callbacks:
            future.add_done_callback(fn, context=context)
        return future

    def __rotate(self) -> None:
        self._present, self._future = self._future, self._create_future()


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

    def _schedule_anext() -> None:
        if not sink.done() and not sink.cancelled():
            result = sink.get_loop().create_task(anext(source))
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
            logger.warning(
                'ensure_perpetual __anext__ future was unexpectedly '
                'cancelled'
            )
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
