from __future__ import annotations

__all__ = (
    'StateError',
    'StateVar',

    'statezip',
)

import abc
import asyncio
import functools
import inspect
from typing import (
    Any,
    AsyncIterable,
    AsyncIterator,
    Awaitable,
    Callable,
    Coroutine,
    Final, Generator,
    Generic,
    Hashable,
    overload,
    Protocol,
    TypeVar,
    Union,
)

from typing_extensions import ParamSpec, TypeAlias

from ._aio_utils import get_event_loop
from ._typing import Maybe, Nothing
from .abuiltins import aiter, anext


_T = TypeVar('_T')
_VT = TypeVar('_VT')
_RT = TypeVar('_RT')

_S = TypeVar('_S', bound=Hashable)
_RS = TypeVar('_RS', bound=Hashable)

_P = ParamSpec('_P')


_FutureOrCoro: TypeAlias = Union[asyncio.Future[_T], Coroutine[Any, Any, _T]]
_MaybeAsync: TypeAlias = Union[Callable[_P, _T], Callable[_P, Awaitable[_T]]]


class _AlwaysAsync(_Fn[_VT, _T], Generic[_VT, _T]):
    __slots__ = ('__wrapped__', '__name__', 'is_async')

    __wrapped__: _Fn[_VT, _T] | _Fn[_VT, Awaitable[_T]]
    is_async: bool | None

    def __init__(self, func: _Fn[_VT, _T] | _Fn[_VT, Awaitable[_T]]) -> None:
        self.__wrapped__ = func
        self.__name__ = func.__name__

        self.is_async = asyncio.iscoroutinefunction(func) or None

    async def __call__(self, *args: _VT) -> _T:
        res = self.__wrapped__(*args)
        if (is_async := self.is_async) is None:
            is_async = self.is_async = inspect.isawaitable(res)

        return (await res) if is_async else res


@functools.cache
def _get_loop() -> asyncio.AbstractEventLoop:
    return get_event_loop()


def _create_future(
    coro: Maybe[_FutureOrCoro[_S]] = Nothing,
) -> asyncio.Future[_S]:
    if coro is Nothing:
        return _get_loop().create_future()
    elif isinstance(coro, asyncio.Future):
        return coro
    elif inspect.isawaitable(coro):
        return asyncio.create_task(coro)
    else:
        raise TypeError(f'a future or coroutine was expected, got {coro!r}')


class StateError(asyncio.InvalidStateError):
    pass


class SimpleStateBase(Awaitable[_S], Generic[_S]):
    __slots__ = ()

    @abc.abstractmethod
    def __await__(self) -> Generator[Any, None, _S]:
        ...

    @abc.abstractmethod
    def set(self, state: _S):
        ...

    @abc.abstractmethod
    def _get_future(self) -> asyncio.Future[_S]:
        ...

    @property
    def _raw_value(self) -> _S | BaseException | None:
        """For pattern matching."""
        fut = self._get_future()
        if not fut.done():
            return None
        try:
            return fut.result()
        except BaseException as exc:
            return exc

    def __await__(self) -> Generator[Any, None, _S]:
        return self._get_future().__await__()

    def __bool__(self) -> bool:
        return bool(self._raw_value)

    def __repr__(self) -> str:
        return f'<{type(self).__name__}{self._format()} at {id(self):#x}>'

    def __str__(self) -> str:
        return f'<{type(self).__name__}{self._format()}>'

    # State information

    @property
    def readonly(self) -> bool:
        """Returns true if the state value has a coro or task that will set the
        value in the future.
        """
        return isinstance(self._get_future(), asyncio.Task)

    # Setter

    def set(self, state: _S) -> None:
        fut = self._get_future()

        if fut.done():
            # ensure any set exceptions are raised if, or if cancelled,
            # asyncio.CancelledError is raised
            _state = fut.result()

            # otherwise, it's already set, which can only be done once
            raise StateError(f'state value is already set: {_state.result()!r}')

        elif isinstance(fut, asyncio.Task):
            raise StateError(
                f'state value is readonly: it will be set from its coroutine: '
                f'{fut.get_coro()!r}'
            )

        fut.set_result(state)

    # Internal methods

    def _format(self) -> str:
        fut = self._get_future()

        if fut.done():
            return f'({self._raw_value!r})'

        return f'({fut!r})'


class SimpleStateValue(SimpleStateBase[_S], Generic[_S]):
    __slots__ = ('_future', )

    _future: asyncio.Future[_S]

    def __init__(self, coro: Maybe[_FutureOrCoro[_S]] = Nothing) -> None:
        self._future = _create_future(coro)

    def _get_future(self) -> asyncio.Future[_S]:
        return self._future


class StateBase(SimpleStateBase[_S], Generic[_S]):
    __slots__ = (
        'is_done',
        'is_set',
        'is_cancelled',
        'is_error',
    )

    is_done: Final[SimpleStateValue[bool]]
    is_set: Final[SimpleStateValue[bool]]
    is_cancelled: Final[SimpleStateValue[bool]]
    is_error: Final[SimpleStateValue[bool]]

    def __init__(self) -> None:
        self.is_done = SimpleStateValue(self._once_done())
        self.is_set = SimpleStateValue(self._once_set())
        self.is_cancelled = SimpleStateValue(self._once_cancelled())
        self.is_error = SimpleStateValue(self._once_error())

    def __setattr__(self, key: str, value: Any):
        if hasattr(self, key) and key in StateBase.__slots__:
            raise AttributeError(f'can\'t set state attribute {key}')
        super().__setattr__(key, value)

    @abc.abstractmethod
    def _get_future(self) -> asyncio.Future[_S]:
        ...

    async def _wait_for_future_state(
        self,
        result: bool,
        cancelled: bool,
        error: bool,
        stopped: bool | None = None,
        future: asyncio.Future[_S] | None = None
    ) -> bool:
        if future is None:
            future = self._get_future()
        try:
            await future
        except (SystemExit, KeyboardInterrupt):
            raise
        except asyncio.CancelledError:
            return cancelled
        except (StopIteration, StopAsyncIteration):
            return error if stopped is None else None
        except BaseException:  # noqa
            return error
        else:
            return result

    async def _once_done(self) -> bool:
        return await self._wait_for_future_state(True, True, True)

    async def _once_set(self) -> bool:
        return await self._wait_for_future_state(True, False, False)

    async def _once_cancelled(self) -> bool:
        return await self._wait_for_future_state(False, True, False)

    async def _once_error(self) -> bool:
        return await self._wait_for_future_state(False, False, True)


class StateValue(StateBase[_S], Generic[_S]):
    __slots__ = ('_future',)

    _future: asyncio.Future[_S]

    def __init__(self, coro: Maybe[_FutureOrCoro[_S]] = Nothing) -> None:
        self._future = _create_future(coro)
        super().__init__()

    def _get_future(self) -> asyncio.Future[_S]:
        return self._future


class StateVar(AsyncIterator[_S], StateBase[_S], Generic[_S]):
    __slots__ = (
        'is_finished',

        '_producer',
        '_value',
        '_value_next',
    )

    _producer: Maybe[AsyncIterator[_S]]

    _value: StateValue[_S]
    _value_next: StateValue[_S]

    is_finished: Final[SimpleStateValue[bool]]

    def __init__(self, producer: Maybe[AsyncIterable[_S]] = Nothing) -> None:
        if producer is not Nothing:
            producer = producer.__aiter__()
        self._producer = producer

        self._value = self.__schedule_next()

        super().__init__()

        self.is_finished = SimpleStateValue(self._once_finished())

    def __await__(self) -> Generator[Any, None, _S]:
        return self._value.__await__()

    def __aiter__(self: _S) -> AsyncIterator[_S]:
        async def _aiter():
            try:
                yield await self._value

                while True:
                    yield await self._value_next

            except (StopAsyncIteration, asyncio.CancelledError):
                pass  #

        return _aiter()

    async def __anext__(self) -> _S:
        try:
            return await self._value_next
        except asyncio.CancelledError:
            raise StopAsyncIteration

    def next(self) -> StateVar[_S]:
        """Returns a statevar that has no value set yet, so that awaiting it
        will return when the next / future state is set.

        If currently there is no value, this state variable is returned,
        otherwise a new state variable
        """
        if self._value_next.is_done:
            self._get_future().result()  # noqa

        async def producer():
            await self._value
            while True:
                try:
                    yield await self._value_next
                except StopAsyncIteration:
                    break

        return StateVar(producer())

    def set(self, state: _S) -> bool:
        """Set the new state. If the state is equal to the current state,
        false is returned. Otherwise, the state is set, the waiters will
        be notified, and true is returned.

        Raises StateError if pending.

        Not threadsafe.
        """
        if state is Nothing:
            raise TypeError('cannot set state to nothing')
        if self.readonly:
            raise StateError(f'{self!r} is read-only')
        if self.is_done:
            raise StateError(f'{self!r} is done')

        value, value_next = self._value, self._value_next

        assert not value_next.is_set

        if state == value:
            return False

        self.__schedule_next()

        value_next.set(state)
        self._value = value_next

        return True

    # Chaining methods

    def map(self, function: _MaybeAsync[[_S], _RS]) -> StateVar[_RS]:
        """Alias of ``statemap(function, self)``"""

        afunction: Callable[[_S], Awaitable[_RS]] = _AlwaysAsync(function)

        async def producer() -> AsyncIterator[_RS]:
            async for state in self:
                yield await afunction(state)

        return StateVar(producer())

    def filter(
        self,
        function: _Fn[_S, bool] | _Fn[_S, Awaitable[bool]]
    ) -> StateVar[_S]:
        afunction: Callable[[_S], Awaitable[_RS]] = _AlwaysAsync(function)

        async def producer() -> AsyncIterator[_RS]:
            async for state in self:
                if await afunction(state):
                    yield state

        return StateVar(producer())

    @overload
    def reduce(
        self,
        function: _MaybeAsync[[_RS, _S], _RS],
        initial: _RS,
    ) -> StateVar[_RS]:
        ...

    @overload
    def reduce(
        self,
        function: _MaybeAsync[[_S, _S], _S],
    ) -> StateVar[_S]:
        ...

    def reduce(
        self,
        function: _MaybeAsync[[_S, _S], _S] | _MaybeAsync[[_RS, _S], _RS],
        initial: Maybe[_RS] = Nothing,
    ) -> StateVar[_RS] | StateVar[_S]:
        """Statevar analogue of functools.reduce."""
        afunction: Callable[[_S], Awaitable[_RS]] = _AlwaysAsync(function)

        async def _reduce() -> AsyncIterator[_RS]:
            it = aiter(self)

            if initial is Nothing:
                try:
                    res = await anext(it)
                except StopAsyncIteration:
                    return
            else:
                res = initial

            yield res

            async for state in it:
                res = await afunction(res, state)
                yield res

        return StateVar(_reduce())

    def _get_future(self) -> asyncio.Future[_S]:
        # noinspection PyProtectedMember
        return self._value_next._get_future()

    async def _once_done(self) -> bool:
        return await self._wait_for_future_state(False, True, True)

    async def _once_set(self) -> bool:
        return await self._value.is_set

    async def _once_error(self) -> bool:
        return await self._wait_for_future_state(False, False, True, False)

    async def _once_finished(self) -> bool:
        return await self._wait_for_future_state(False, False, False, True)

    def _format(self) -> str:
        # noinspection PyProtectedMember
        return self._value._format()

    def __schedule_next(self) -> StateValue[_S]:
        # assert not hasattr(self, '_value_next') or self._value_next.is_set

        if self._producer is Nothing:
            value_next = self._value_next = StateValue()
            return value_next

        # Chicken & Egg Problem: Solved
        feathery_wormhole: asyncio.Future[StateValue[_S]]
        feathery_wormhole = _create_future()

        async def _hatch():
            # Wait for the egg, but don't touch it! Unless you want spacetime
            # to fold into a singularity...
            interdimensional_egg: StateValue[_S] = await feathery_wormhole

            try:
                chick: _S = await self._producer.__anext__()
            except (StopIteration, StopAsyncIteration, asyncio.CancelledError):
                # "acceptable" exit condition, RIP :(
                pass
            except (KeyboardInterrupt, SystemExit):
                # Universe is imploding; raise ASAP
                raise
            except BaseException:  # noqa
                # *The egg is a Lie* - make both __await__ and __anext__ raise
                self._value = interdimensional_egg
            else:
                # success! what was the future is now the present, and the
                # future is beyond the future of the past
                assert self._value_next is interdimensional_egg

                # repeat and rinse (:
                _get_loop().call_soon(self.__schedule_next)

                # he'll hatch soon, so let's put it in the right place
                self._value = interdimensional_egg

                return chick
                # the interdimensional egg is hatched

        egg = self._value_next = StateValue(_hatch())

        feathery_wormhole.set_result(egg)

        return egg


