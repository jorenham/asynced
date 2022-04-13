from __future__ import annotations

__all__ = ('StateVar', 'StateTuple', 'StateDict')

import abc
import asyncio
import itertools
from collections import UserDict
from typing import (
    AsyncIterable,
    AsyncIterator,
    Awaitable,
    Callable,
    cast, ClassVar,
    Collection,
    Final,
    Generic,
    Hashable,
    ItemsView, Iterable,
    KeysView, Mapping, NoReturn,
    overload,
    Sequence,
    TypeVar,
    Union,
)
from typing_extensions import TypeAlias

from ._states import State, StateCollection
from ._typing import (
    Comparable,
    Maybe,
    Nothing,
    NothingType,
)
from .asyncio_utils import race
from .exceptions import StopAnyIteration


_T = TypeVar('_T')
_K = TypeVar('_K')

_S = TypeVar('_S', bound=Hashable)
_S_co = TypeVar('_S_co', bound=Hashable, covariant=True)

_SS = TypeVar('_SS', bound=Collection)

_RS = TypeVar('_RS', bound=Hashable)


_Counter: TypeAlias = Callable[[], int]


class StateVar(State[_S], Generic[_S]):
    __slots__ = ('_name', '_i', '_producer', '_consumer')

    _task_counter: ClassVar[_Counter] = itertools.count(0).__next__

    _name: Final[str]
    _i: int

    _producer: Maybe[AsyncIterable[_S]]
    _consumer: Final[Maybe[asyncio.Task[None | NoReturn]]]

    def __init__(
        self,
        producer: Maybe[StateVar[_S] | AsyncIterable[_S]] = Nothing,
        *,
        key: Callable[[_S], Comparable] = lambda s: s,
        name: str | None = None,
        start: int | None = None,
    ) -> None:
        super().__init__(key=key)

        if name is None:
            self._name = f'{type(self).__name__}_{self._task_counter()}'
        else:
            self._name = name

        if producer is not Nothing:
            producer = producer
            consumer = asyncio.create_task(
                self._consume(),
                name=f'{self._name}.consumer'
            )
        else:
            consumer = Nothing

        self._producer = producer
        self._consumer = consumer

        if start is None and isinstance(producer, type(self)):
            if other_at := producer.at(None):
                start = other_at - 1  # -1 because we have no value yet

        self._i = 0 if start is None else start

    @property
    def name(self) -> str:
        return self._name

    def at(self, default: Maybe[_T] = Nothing) -> int | _T:
        """Get current iteration count, starting at 0.

        If not set, raises LookupError.
        """
        # TODO return an int-like statevar
        if hasattr(self, '_value'):
            future = self.future
            if future.done() and not future.cancelled():
                return self._i

        if default is not Nothing:
            return default

        raise LookupError(repr(self))

    def next_at(self) -> int:
        """Return the next iteration count, starting
         at 0."""
        return self.at(self._i - 1) + 1

    @overload
    def get(self, default: NothingType = ...) -> _S: ...
    @overload
    def get(self, default: _T = ...) -> _S | _T: ...

    def get(self, default: Maybe[_T] = Nothing) -> _S | _T:
        """Return the current state value.

        If not set, the method will return the default value of the
        `default`
        argument of the method, if provided; or raise a LookupError.
        """
        return self._get(default)

    def map(
        self,
        function: Callable[[_S], _RS] | Callable[[_S], Awaitable[_RS]],
    ) -> StateVar[_RS]:
        # TODO docstring
        name = f'{function.__name__}({self._name})'
        itr = cast(AsyncIterator[_RS], self._map(function))
        return StateVar(itr, name=name)

    def split(self: StateVar[Sequence[_RS]]) -> StateTuple[_RS]:
        # TODO docstring
        n = len(self.get())

        async def producer(i: int):
            async for state in self:
                yield state[i]

        return StateTuple(map(producer, range(n)))

    @property
    def _is_done(self) -> bool:
        consumer = self._consumer
        if consumer is not Nothing and consumer.done():
            return True

        return super()._is_done

    def _get_task_name(self) -> str:
        return self._name

    def _check(self) -> None | NoReturn:
        super()._check()

        consumer = self._consumer
        if consumer is not Nothing and consumer.done():
            if consumer.exception() is not None:
                consumer.result()  # raises exception
            elif consumer.cancelled() and not self._is_set:
                consumer.result()  # raises asyncio.CancelledError

    def _check_next(self) -> None | NoReturn:
        consumer = self._consumer
        if consumer is not Nothing and consumer.done():
            consumer.result()
            raise StopAsyncIteration

        super()._check_next()

    async def _consume(self) -> None | NoReturn:
        assert self._producer is not Nothing
        try:
            async for state in self._producer:
                self._set(state)
        except (SystemExit, KeyboardInterrupt) as exc:
            self._raise(exc)
            raise
        except GeneratorExit:
            # don't attempt to "stop" if the loop was closed
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                self._cancel()
            else:
                self._stop()
        except StopAnyIteration:
            self._stop()
        except asyncio.CancelledError:
            self._cancel()
        except BaseException as exc:
            self._raise(exc)
        else:
            self._stop()

    def _on_set(self, state: Maybe[_S] = Nothing) -> None:
        self._i += 1

        super()._on_set(state)

        for j, parent in self._collections:
            # noinspection PyProtectedMember
            parent._on_item_set(j, state)

    def _on_stop(self) -> None:
        super()._on_stop()

        for j, parent in self._collections:
            # noinspection PyProtectedMember
            parent._on_item_stop(j)

    def _on_error(self, exc: BaseException) -> None:
        super()._on_error(exc)

        for j, parent in self._collections:
            # noinspection PyProtectedMember
            parent._on_item_error(j, exc)

    def _on_cancel(self) -> None:
        super()._on_cancel()

        for j, parent in self._collections:
            # noinspection PyProtectedMember
            parent._on_item_cancel(j)


class StateTuple(StateCollection[int, _S, tuple[_S, ...]], Generic[_S]):
    __slots__ = ('_states', )

    _states: tuple[StateVar[_S], ...]

    def __init__(
        self,
        producers: int | Iterable[StateVar[_S] | AsyncIterable[_S]],
        *,
        key: Callable[[tuple[_S, ...]], Comparable] = lambda ss: ss,
    ) -> None:
        super().__init__(key=key)

        if isinstance(producers, int):
            states = [StateVar() for _ in range(producers)]
        else:
            states = []
            for producer in producers:
                if isinstance(producer, StateVar):
                    statevar = producer
                elif isinstance(producer, StateTuple):
                    raise TypeError(
                        f'{type(self).__name__!r} cannot contain itself'
                    )
                elif not isinstance(producer, AsyncIterable):
                    raise TypeError(
                        f'expected an iterable of StateVar\'s or async '
                        f'iterables, iterable contains '
                        f'{type(producer).__name__!r} instead'
                    )
                else:
                    statevar = StateVar(producer)

                # noinspection PyProtectedMember
                states.append(statevar)

        if not states:
            raise TypeError(
                f'{type(self).__name__}() requires at least one item.'
            )

        for i, state in enumerate(states):
            # noinspection PyProtectedMember
            state._collections.append((i, self))

        self._states = tuple(states)

    def __hash__(self) -> int:
        return hash(self._states)

    def __contains__(self, item: object) -> bool:
        if isinstance(item, State):
            return item in self._states

        default = object()
        return item in tuple(
            s for s in self._get_values(default)
            if s is not default
        )

    def __reversed__(self) -> StateTuple[_S]:
        return self[::-1]

    def __add__(self, other: StateTuple[_S]):
        cls = type(self)
        if not isinstance(other, StateTuple):

            raise TypeError(
                f'can only concatenate {cls.__name__} (not '
                f'{type(other).__name__!r}) to {cls.__name__}'
            )

        return cls(self._states + other._states)

    def __mul__(self, value: int) -> StateTuple[_S]:
        """Return self * value"""
        if not isinstance(value, int):
            raise TypeError(
                f'can\'t multiply {type(self).__name__} by non-int of type '
                f'{type(value).__name__}'
            )
        return type(self)(self._states * value, key=self._key)

    def __rmul__(self, value: int) -> StateTuple[_S]:
        """Return value * self"""
        return self.__mul__(value)

    @overload
    def __getitem__(self, __k: int) -> StateVar[_S]: ...
    @overload
    def __getitem__(self, __ks: tuple[int]) -> StateTuple[_S]: ...
    @overload
    def __getitem__(self, __ks: slice) -> StateTuple[_S]: ...

    def __getitem__(
        self,
        index: int | tuple[int] | slice
    ) -> State[_S] | StateTuple[_S]:
        if isinstance(index, int):
            return super().__getitem__(index)
        elif isinstance(index, tuple):
            return type(self)((self[i] for i in index), key=self._key)
        elif isinstance(index, slice):
            return type(self)(
                (self[i] for i in range(*index.indices(len(self)))),
                key=self._key
            )
        else:
            raise TypeError(
                f'{type(self).__name__} indices must be integers or slices, '
                f'not {type(index).__name__}'
            )
        
    @property
    def readonly(self) -> bool:
        """.set() cannot be used, but unless the statevar items are readable,
        they can be set with e.g. self[0] = ...
        """
        return True

    def get(self, key: int, default: Maybe[_T] = Nothing) -> _S | _T:
        # noinspection PyProtectedMember
        return self._states[key]._get(default)

    def map(
        self,
        function: Union[
            Callable[[tuple[_S, ...]], _RS],
            Callable[[tuple[_S, ...]], Awaitable[_RS]]
        ],
        *,
        key: Callable[[_RS], Comparable] = lambda r: r
    ) -> StateVar[_RS]:
        return StateVar(cast(AsyncIterator[_RS], self._map(function)), key=key)

    def starmap(
        self,
        function: Callable[..., _RS] | Callable[..., Awaitable[_RS]],
        *,
        key: Callable[[_RS], Comparable] = lambda r: r
    ) -> StateVar[_RS]:
        itr = cast(AsyncIterator[_RS], self._map(lambda args: function(*args)))
        return StateVar(itr, key=key)

    # PyCharm doesn't understand walrus precedence:
    # noinspection PyRedundantParentheses
    async def _get_producer(self) -> AsyncIterator[tuple[_S]]:
        try:
            yield (states := tuple(await asyncio.gather(*self._states)))

            async for i, state in race(*self._states):
                yield (states := states[:i] + (state, ) + states[i+1:])

        except StopAsyncIteration:
            pass

    def _get_states(self) -> tuple[StateVar[_S], ...]:
        return self._states

    def _get_values(self, default: Maybe[_S] = Nothing) -> tuple[_S, ...]:
        return tuple(sv.get(default) for sv in self._states)


class StateDict(
    UserDict[_K, State[_S]],
    StateCollection[_K, _S, dict[_K, _S]],
    Generic[_K, _S],
):
    __slots__ = ('_states',)

    _states: dict[_K, State[_S]]

    def __init__(
        self,
        mapping: Mapping[_K, State[_S]] | AsyncIterable[tuple[_K, _S]],
        /,
        **states: State[_S],
    ) -> None:
        super().__init__()

        states = {}
        for key, state in states.items():
            if not isinstance(state, State):
                raise TypeError(
                    f'expected a StateVar or async iterables, got '
                    f'{type(state).__name__!r} instead'
                )

            states[key] = state

        self._states = states

    @overload
    @abc.abstractmethod
    def get(self, key: _K, default: NothingType = ...) -> _S: ...
    @overload
    @abc.abstractmethod
    def get(self, key: _K, default: _T = ...) -> _S | _T: ...

    def get(self, key: _K, default: Maybe[_T] = Nothing) -> _S | _T:
        return self._states[key]._get(default)

    def items(self) -> ItemsView[_K, _S]:
        # TODO
        ...

    def keys(self) -> KeysView[_K]:
        return self._states.keys()

    def _get_states(self) -> dict[_K, State[_S]]:
        return self._states

    def _get_values(self, default: Maybe[_T] = Nothing) -> dict[_K, _S | _T]:
        return {k: s._get(default) for k, s in self._states.items()}
