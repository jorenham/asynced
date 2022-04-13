from __future__ import annotations

__all__ = ('StateVar', 'StateTuple', 'StateDict')

import abc
import asyncio
import itertools
import operator
from typing import (
    Any, AsyncIterable,
    AsyncIterator,
    Awaitable,
    Callable,
    cast, ClassVar,
    Collection,
    Final,
    Generator, Generic,
    Hashable,
    ItemsView, Iterable,
    KeysView, Mapping,
    NoReturn,
    overload,
    Sequence,
    TypeVar,
    Union, ValuesView,
)
from typing_extensions import Self, TypeAlias

from ._states import State, StateCollection
from ._typing import (
    Comparable,
    Maybe,
    Nothing,
    NothingType,
)
from .asyncio_utils import race
from .exceptions import StateError, StopAnyIteration


_T = TypeVar('_T')
_K = TypeVar('_K')

_S = TypeVar('_S', bound=Hashable)
_S_co = TypeVar('_S_co', bound=Hashable, covariant=True)
_N = TypeVar('_N', bool, int)
_SN = TypeVar('_SN', bound='StateNumber')

_SS = TypeVar('_SS', bound=Collection)

_RS = TypeVar('_RS', bound=Hashable)


_Counter: TypeAlias = Callable[[], int]


class StateVar(State[_S], Generic[_S]):
    __slots__ = ('_name', '_i', '_producer', '_consumer')

    _task_counter: ClassVar[_Counter] = itertools.count(0).__next__

    _name: Final[str]
    _i: int

    _producer: Maybe[AsyncIterable[_S]]
    _consumer: Maybe[asyncio.Task[None | NoReturn]]

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

        self._producer = Nothing
        self._consumer = Nothing

        if producer is not None:
            self.set_from(producer)
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

    def set_from(self, producer: StateVar[_S] | AsyncIterable[_S]):
        if self._producer is not Nothing:
            raise StateError(f'{self!r} is already being set')

        self._check()

        self._producer = producer
        self._consumer = asyncio.create_task(
            self._consume(),
            name=f'{self._name}.consumer'
        )

    def map(
        self,
        function: Callable[[_S], _RS] | Callable[[_S], Awaitable[_RS]],
    ) -> StateVar[_RS]:
        # TODO docstring
        name = f'{function.__name__}({self._name})'
        itr = cast(AsyncIterator[_RS], self._map(function))
        return type(self)(itr, name=name)

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


class StateNumber(StateVar[_N]):
    __slots__ = ()

    def __init__(
        self,
        producer: Maybe[_N | StateVar[_N] | AsyncIterable[_N]] = Nothing,
        *,
        name: str | None = None,
        start: int | None = None,
        default: Maybe[_N] = Nothing
    ) -> None:
        if isinstance(producer, int):
            value = self._coerce(producer)
            producer = Nothing
        else:
            value = None

        super().__init__(producer, name=name, start=start)

        if value is not None:
            self.set(value)

        self._default = default

    def __await__(self) -> Generator[Any, None, _N]:
        return self._wait().__await__()

    def __bool__(self) -> bool:
        return bool(self._get(False))

    def __int__(self) -> int:
        return int(self._get(0))

    def __float__(self) -> float:
        return float(self.__int__())

    def __pos__(self) -> StateInt:
        return cast(StateInt, self.map(lambda i: +i))

    def __neg__(self) -> StateInt:
        return cast(StateInt, self.map(lambda i: -i))

    def __abs__(self) -> StateInt:
        return cast(StateInt, self.map(lambda i: abs(i)))

    def __eq__(self, other: Any) -> StateBool:
        return self._map_with(operator.eq, other, '{} == {}', cls=StateBool)

    def __ne__(self, other: Any) -> StateBool:
        return self._map_with(operator.ne, other, '{} != {}', cls=StateBool)

    def __add__(self, other: int | StateNumber[int]) -> StateInt:
        return self._map_with(operator.add, other, '{} + {}')

    __radd__ = __add__

    def __iadd__(self, other: int) -> Self:
        return self._apply_with(operator.add, other)

    def __mul__(self, other: int | StateNumber[int]) -> StateInt:
        return self._map_with(operator.mul, other, '{} * {}')

    __rmul__ = __mul__

    def __imul__(self, other: int) -> Self:
        return self._apply_with(operator.mul, other)

    def __mod__(self, other: int | StateNumber[int]) -> StateInt:
        return self._map_with(operator.mod, other, '{} % {}')

    def __rmod__(self, other: int | StateNumber[int]) -> StateInt:
        return self._map_with(operator.mod, other, '{} % {}', reverse=True)

    def __imod__(self, other: int) -> Self:
        return self._apply_with(operator.mod, other)

    def __pow__(self, other: int | StateNumber[int]) -> StateInt:
        if (value := self.get(0)) < 0:
            raise ValueError(f'{value!r} < 0')

        return self._map_with(operator.pow, other, '{} ** {}')

    def __rpow__(self, other: int | StateNumber[int]) -> StateInt:
        if other < 0:
            raise ValueError(f'{other!r} < 0')

        return self._map_with(operator.pow, other, '{} % {}', reverse=True)

    def __ipow__(self, other: int) -> Self:
        if other < 0:
            raise ValueError(f'{other!r} < 0')

        return self._apply_with(operator.pow, other)

    def _map_with(
        self,
        op: Callable[[int, _S], Any],
        other: StateVar[_S] | _S,
        name: str | None = None,
        reverse: bool = False,
        cls: type[_SN] | None = None
    ) -> _SN:
        if cls is None:
            cls = StateInt

        if isinstance(other, StateVar):
            args = (other, self) if reverse else (self, other)
            return cls(
                StateTuple(args).starmap(op),
                name=name.format(args[0].name, args[1].name) if name else None
            )

        if reverse:
            names = repr(other), self._name
            producer = self._map(lambda a: op(other, a))
        else:
            names = self._name, repr(other)
            producer = self._map(lambda a: op(a, other))

        return cls(
            producer,
            name=name.format(*names) if name else None,
        )

    def _apply(self, op: Callable[[_N], int]) -> Self:
        """In-place analogue of map()"""
        res = self._coerce(op(self.get()))
        self.set(res)

        return self

    def _apply_with(
        self, op:
        Callable[[_N, int], int],
        other: _N
    ) -> Self:
        """In-place analogue of map_with()"""
        if not isinstance(other, int):
            raise TypeError(f'expected an int, got {type(other).__name__!r}')

        res = self._coerce(op(self.get(), other))
        self.set(res)

        return self

    @abc.abstractmethod
    def _coerce(self, other: int) -> _N: ...

    def _set(self, value: _S, always: bool = False) -> None:
        super()._set(self._coerce(value))

    async def _wait(self) -> _N:
        async for num in self:
            if num:
                return num

            await asyncio.sleep(0)

        raise LookupError(repr(self))


class StateInt(StateNumber[int]):
    __slots__ = ()

    def __init__(
        self,
        producer: Maybe[int | StateVar[int] | AsyncIterable[int]],
        *,
        name: str | None = None,
        start: int | None = None,
    ) -> None:
        super().__init__(producer, name=name, start=start)

    def __lt__(self, other: int | StateInt) -> StateBool:
        return self._map_with(operator.lt, other, '{} < {}', cls=StateBool)

    def __le__(self, other: int | StateInt) -> StateBool:
        return self._map_with(operator.le, other, '{} <= {}', cls=StateBool)

    def __gt__(self, other: int | StateInt) -> StateBool:
        return self._map_with(operator.gt, other, '{} > {}', cls=StateBool)

    def __ge__(self, other: int | StateInt) -> StateBool:
        return self._map_with(operator.ge, other, '{} >= {}', cls=StateBool)

    def _coerce(self, other: int) -> int:
        return int(other)

    def _equals(self, state: int) -> bool:
        return self.get() == int(state)


class StateBool(StateNumber[bool]):
    __slots__ = ()

    def __init__(
        self,
        producer: Maybe[bool | StateVar[bool] | AsyncIterable[bool]],
        *,
        name: str | None = None,
        start: int | None = None,
        default: bool = False,
    ) -> None:
        super().__init__(producer, name=name, start=start)

    def __inv__(self) -> StateBool:
        async def _inv():
            async for b in self:
                yield not b

        return StateBool(_inv(), name=f'not {self._name}')

    @overload
    def __and__(self, other: bool | StateBool) -> StateBool: ...
    @overload
    def __and__(self, other: int | StateInt) -> StateInt: ...

    def __and__(
        self,
        other: bool | int | StateBool | StateInt
    ) -> StateBool | StateInt:
        cls = StateBool if isinstance(other, (bool, StateBool)) else StateInt
        return self._map_with(operator.and_, other, '{} and {}', cls=cls)

    @overload
    def __or__(self, other: bool | StateBool) -> StateBool: ...
    @overload
    def __or__(self, other: int | StateInt) -> StateInt: ...

    def __or__(
        self,
        other: bool | int | StateBool | StateInt
    ) -> StateBool | StateInt:
        cls = StateBool if isinstance(other, (bool, StateBool)) else StateInt
        return self._map_with(operator.or_, other, '{} or {}', cls=cls)

    __rand__ = __and__
    __ror__ = __or__

    not_ = __inv__
    and_ = __and__
    or_ = __or__

    def _coerce(self, other: int) -> bool:
        return bool(other)

    def _equals(self, state: bool) -> bool:
        return self.get() is bool(state)


class StateTuple(StateCollection[int, _S, tuple[_S, ...]], Generic[_S]):
    __slots__ = ('_states', )

    _states: tuple[StateVar[_S], ...]

    def __init__(
        self,
        producers: int | Iterable[StateVar[_S] | AsyncIterable[_S]],
    ) -> None:
        # TODO key
        super().__init__()

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
            s for s in self._get_data(default)
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
        return type(self)(self._states * value)

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
            return type(self)(self[i] for i in index)
        elif isinstance(index, slice):
            return type(self)(self[i] for i in range(*index.indices(len(self))))
        else:
            raise TypeError(
                f'{type(self).__name__} indices must be integers or slices, '
                f'not {type(index).__name__}'
            )

    def __setitem__(self, key: int, value: StateVar[_S] | _S):
        if isinstance(value, StateVar):
            self[key].set_from(value)
        else:
            self[key].set(value)

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
        key: Callable[[_RS], Comparable] = lambda r: r,
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

    def _get_data(self, default: Maybe[_S] = Nothing) -> tuple[_S, ...]:
        return tuple(sv.get(default) for sv in self._states)


class StateDict(
    Mapping[_K, State[_S]],
    StateCollection[_K, _S, dict[_K, _S]],
    Generic[_K, _S],
):
    __slots__ = ('_states',)

    _states: dict[_K, State[_S]]
    _count: Final[StateInt]

    def __init__(
        self,
        mapping: Maybe[Union[
            Mapping[_K, State[_S]],
            AsyncIterable[tuple[_K, _S]]
        ]] = Nothing,
        /,
        **states: State[_S],
    ) -> None:
        if mapping is not Nothing:
            raise NotImplementedError('TODO')

        super().__init__()

        self._states = {}
        for key, state in states.items():
            if not isinstance(state, State):
                raise TypeError(
                    f'expected a State or async iterables, got '
                    f'{type(state).__name__!r} instead'
                )

            state._collections.append((key, self))

            self._states[key] = state

        self._count = StateInt(self._get_counter(), name=f'len({self!r})')
        self._update_count()

    def __len__(self) -> StateInt:
        return self._count

    def __contains__(self, key: _K) -> bool:
        return key in self._states and key in self._items_set

    def __getitem__(self, key: _K) -> State[_S]:
        states = self._states
        if key in states:
            return states[key]

        return self.__missing__(key)

    def __setitem__(self, key: _K, value: State[_S] | _S) -> None:
        if isinstance(value, State):
            if key in self._states:
                state = self._states[key]
                if key in self._items_set or not isinstance(state, StateVar):
                    raise KeyError(f'{key!r} already set')

                state.set_from(value)

            else:
                self._states[key] = value
                value._collections.append((key, self))
        else:
            self[key].set(key)

    def __delitem__(self, key: _K) -> None:
        if key in self._states:
            state = self._states[key]
            state._cancel()

            state._collections.remove((key, self))
            del self._states[key]

            self._on_item_del(key)

        else:
            raise KeyError(key)

    def __missing__(self, key: _K) -> StateVar[_S]:
        assert key not in self._states
        state = StateVar()
        state._collections.append((key, self))
        self._states[key] = state
        return state

    def items(self) -> ItemsView[_K, _S]:
        return self._get_states().items()

    def keys(self) -> KeysView[_K]:
        return self._get_states().keys()

    def values(self) -> ValuesView[_S]:
        return self._get_states().values()

    @overload
    @abc.abstractmethod
    def get(self, key: _K, default: NothingType = ...) -> _S: ...

    @overload
    @abc.abstractmethod
    def get(self, key: _K, default: _T = ...) -> _S | _T: ...

    def get(self, key: _K, default: Maybe[_T] = Nothing) -> _S | _T:
        return self._states[key]._get(default)

    def clear(self) -> None:
        states = self._states

        for key, state in states.items():
            state._collections.remove((key, self))

        self._states.clear()
        self._update_count()

    def _get_states(self) -> dict[_K, State[_S]]:
        return {
            k: s for k, s in self._states.items()
            if s.future.done() and not s.future.cancelled()
        }

    def _get_data(self, default: Maybe[_T] = Nothing) -> dict[_K, _S | _T]:
        if default is Nothing:
            return {k: s for k, s in self._states if k in self._items_set}

        # noinspection PyProtectedMember
        return {k: s._get(default) for k, s in self._states}

    def _update_count(self):
        self._count.set(len(self._get_data()))

    def _on_item_set(self, item: _K, value: Maybe[_S] = Nothing) -> None:
        super()._on_item_set(item, value)
        self._update_count()

    def _on_item_del(self, item: _K) -> None:
        super()._on_item_del(item)
        self._update_count()
