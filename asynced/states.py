from __future__ import annotations

__all__ = (
    'StateVar',
    'StateTuple',
    'StateDict',
    'statefunction',
)


import functools
import itertools

from typing import (
    Any,
    AsyncIterable as AIterable,
    AsyncIterator as AIterator,
    Awaitable,
    Callable,
    cast,
    ClassVar,
    Final,
    Generic,
    get_origin,
    get_type_hints,
    ItemsView,
    Iterable,
    Iterator,
    KeysView,
    Mapping,
    overload,
    Sequence,
    TypeVar,
    Union,
    ValuesView,
)
from typing_extensions import TypeAlias

from ._states import State, StateCollection
from ._typing import Comparable, Maybe, Nothing, NothingType


_T = TypeVar('_T', bound=object)
_K = TypeVar('_K')

_S = TypeVar('_S', bound=object)
_RS = TypeVar('_RS', bound=object)

_Counter: TypeAlias = Callable[[], int]


class StateVar(State[_S], Generic[_S]):
    __slots__ = ('_name',)

    _task_counter: ClassVar[_Counter] = itertools.count(0).__next__

    _name: Final[str]

    def __init__(
        self,
        producer: Maybe[AIterable[_S]] = Nothing,
        *,
        key: Callable[[_S], Comparable] = lambda s: s,
        name: str | None = None,
    ) -> None:
        super().__init__(key=key)

        if name is None:
            self._name = f'{type(self).__name__}_{self._task_counter()}'
        else:
            self._name = name

        self._producer = Nothing
        self._consumer = Nothing

        if producer is not Nothing:
            self.set_from(producer)

    def __bool__(self) -> bool:
        return self.is_set

    @property
    def name(self) -> str:
        return self._name

    @overload
    def get(self) -> _S: ...
    @overload
    def get(self, default: _T) -> _S | _T: ...

    def get(self, default: Maybe[_T] = Nothing) -> _S | _T:
        """Return the current state value.

        If not set, the method will return the default value of the
        `default`
        argument of the method, if provided; or raise a LookupError.
        """
        return self._get(default)

    def set(self, state: _S) -> bool:
        """Set the new state. If the state is equal to the current state,
        false is returned. Otherwise, the state is set, the waiters will
        be notified, and true is returned.

        Raises StateError if readonly.
        """
        self._ensure_mutable()
        self._check_next()

        if not (skip := self._equals(state)):
            self._set(state)

        return not skip

    def set_from(self, state_producer: State[_S] | AIterable[_S]) -> None:
        self._ensure_mutable()

        self._check()
        self._set_from(state_producer)

    def split(self: StateVar[Sequence[_RS]]) -> StateTuple[_RS]:
        n = len(self.get())

        async def producer(i: int):
            async for state in self:
                yield state[i]

        return StateTuple(map(producer, range(n)))

    def _get_task_name(self) -> str:
        return self._name


class StateTuple(StateCollection[int, _S, tuple[_S, ...]], Generic[_S]):
    __slots__ = ('_states', )

    _states: tuple[StateVar[_S], ...]

    @overload
    def __init__(self, __arg: int): ...
    @overload
    def __init__(self, __arg: StateTuple[_S]): ...
    @overload
    def __init__(self, __arg: Iterable[AIterable[_S]]): ...

    def __init__(
        self,
        iterable: int | StateTuple[_S] | Iterable[AIterable[_S]],
    ):
        if isinstance(iterable, int):
            states = [StateVar() for _ in range(iterable)]
        elif isinstance(iterable, StateTuple):
            states = list(iterable)
        elif isinstance(iterable, AIterable):
            # TODO length is unknown now; relax the preset _states restriciton
            raise NotImplementedError()
        else:
            states = []
            for producer in iterable:
                if isinstance(producer, StateVar):
                    statevar = producer
                elif isinstance(producer, StateTuple):
                    raise TypeError(
                        f'{type(self).__name__!r} cannot contain itself'
                    )
                elif not isinstance(producer, AIterable):
                    raise TypeError(
                        f'expected an iterable of StateVar\'s or async '
                        f'iterables, iterable contains '
                        f'{type(producer).__name__!r} instead'
                    )
                else:
                    statevar = StateVar(cast(AIterable[_S], producer))

                # noinspection PyProtectedMember
                states.append(statevar)

        if not states:
            raise TypeError(
                f'{type(self).__name__}() requires at least one item.'
            )

        for i, state in enumerate(states):
            # noinspection PyProtectedMember
            state._collections.append((i, self))

        super().__init__(
            key=lambda ss: tuple(sv._key(s) for s, sv in zip(ss, states))
        )

        self._states = tuple(states)

        if not self.is_set and all(s.is_set for s in states):
            self._set(tuple(s.get() for s in states))

    def __iter__(self) -> Iterator[StateVar[_S]]:
        return iter(self._states)

    def __contains__(self, item: _S) -> bool:
        if isinstance(item, State):
            return item in self._states

        return item in tuple(s.get() for s in self._states if s.is_set)

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
        states = self._states
        if isinstance(index, int):
            return states[index]
        elif isinstance(index, tuple):
            return type(self)(states[i] for i in index)
        elif isinstance(index, slice):
            return type(self)(
                states[i] for i in range(*index.indices(len(states)))
            )
        else:
            raise TypeError(
                f'{type(self).__name__} indices must be integers or slices, '
                f'not {type(index).__name__}'
            )

    def __setitem__(self, index: int, value: State[_S] | _S):
        state = self._states[index]

        if isinstance(value, State):
            state.set_from(value)
        else:
            state.set(value)

    def get(
        self,
        index: Maybe[int] = Nothing,
        /,
        default: Maybe[_T] = Nothing
    ) -> tuple[_S, ...] | _S | _T:
        if index is Nothing:
            if default is not Nothing:
                raise TypeError('default cannot be set if no key is passed')

            return self._get_data()

        return self._states[index].get(default)

    def map(self, function, cls=None, *cls_args, **cls_kwargs):
        if cls is None:
            cls = StateVar

        return super().map(function, cls, *cls_args, **cls_kwargs)

    def starmap(
        self,
        function: Callable[..., Awaitable[_RS]] | Callable[..., _RS],
        cls: type[State[_RS]] | None = None,
        *cls_args: Any,
        **cls_kwargs: Any,
    ) -> State[_RS]:
        return self.map(lambda ss: function(*ss), cls, *cls_args, **cls_kwargs)

    def _get_states(self) -> Mapping[int, State[_S]]:
        return {i: s for i, s in enumerate(self._states)}

    def _get_data(self, default: Maybe[_S] = Nothing) -> tuple[_S, ...]:
        if default is Nothing:
            return tuple(sv.get() for sv in self._states)
        else:
            return tuple(sv.get(default) for sv in self._states)


_StateMap: TypeAlias = Mapping[_K, StateVar[_S]]


class StateDict(
    StateCollection[_K, _S, dict[_K, _S]],
    Mapping[_K, StateVar[_S]],
    Generic[_K, _S],
):
    __slots__ = ('_states',)

    _states: dict[_K, StateVar[_S]]

    @overload
    def __init__(self, __arg: NothingType = ..., /): ...
    @overload
    def __init__(self, __arg: NothingType = ..., /, **__kw: StateVar[_S]): ...
    @overload
    def __init__(self, __arg: _StateMap[_K, _S], /): ...
    @overload
    def __init__(self, __arg: _StateMap[_K, _S], /, **__kw: StateVar[_S]): ...
    @overload
    def __init__(self, __arg: AIterable[tuple[_K, _S | None]], /): ...
    @overload
    def __init__(self, __arg: AIterable[Mapping[_K, _S]], /): ...

    def __init__(
        self,
        mapping: Maybe[Union[
            Mapping[_K, StateVar[_S]],
            AIterable[tuple[_K, _S]],
            AIterable[Mapping[_K, _S]],
        ]] = Nothing,
        /,
        **states: StateVar[_S],
    ):
        producer = Nothing
        if mapping is Nothing:
            initial_states = states
        elif isinstance(mapping, Mapping):
            initial_states = mapping | states
        elif isinstance(mapping, AIterable):
            if states:
                raise TypeError(
                    f'{type(self).__name__}() takes no keyword arguments when '
                    f'an async iterable is given'
                )

            initial_states = {}
            producer = mapping
        else:
            raise TypeError(
                f'{type(mapping).__name__!r} object is not a mapping or '
                f'async iterable'
            )

        initial = {}
        for key, state in initial_states.items():
            if state.is_set and not state.is_error:
                initial[key] = state.get()

        self._states = {}
        for key, state in initial_states.items():
            if not isinstance(state, StateVar):
                raise TypeError(
                    f'expected a StateVar instance, got '
                    f'{type(state).__name__!r} instead'
                )

            state._collections.append((key, self))

            self._states[cast(_K, key)] = state

        self._producer = Nothing
        self._consumer = Nothing

        super().__init__(
            key=lambda ss: {k: self[k]._key(v) for k, v in ss.items()}
        )

        if initial:
            self._set_item(initial)

        if producer is not Nothing:
            self._set_from(producer)

    def __bool__(self) -> bool:
        return self.is_set

    def __iter__(self) -> Iterator[_K]:
        return iter(self._get_states())

    def __aiter__(
        self,
        *,
        buffer: int | None = 4
    ) -> AIterator[dict[_K, _S]]:
        return super().__aiter__(buffer=buffer)

    def __contains__(self, key: _K) -> bool:
        return key in self._states and self._states[key].is_set

    def __getitem__(self, key: _K) -> StateVar[_S]:
        states = self._states

        if key not in states:
            states[key] = self.__missing__(key)

        return states[key]

    def __setitem__(self, key: _K, value: StateVar[_S] | _S) -> None:
        self._ensure_mutable()

        self._set_item((key, value))

    def __delitem__(self, key: _K) -> None:
        self._ensure_mutable()

        if key not in self._states:
            raise KeyError(key)

        self._set_item((key, None))

    def __missing__(self, key: _K) -> StateVar[_S]:
        assert key not in self._states

        state = StateVar()
        state._collections.append((key, self))
        return state

    @property
    def is_set(self) -> bool:
        return len(self) > 0
    
    def items(self) -> ItemsView[_K, StateVar[_S]]:
        return self._get_states().items()

    def keys(self) -> KeysView[_K]:
        return self._get_states().keys()

    def values(self) -> ValuesView[StateVar[_S]]:
        return self._get_states().values()

    async def differences(self) -> AIterator[
        tuple[_K, None, _S] | tuple[_K, _S, _S] | tuple[_K, _S, None]
    ]:
        """
        Returns an async iterator that yields either:

        - (K, None, V) for additions: K not in statedict => statedict[K] = V
        - (K, U, V) for changes: statedict[K] = U => statedict[K] = V
        - (K, U, None) for changes: statedict[K] = U => del statedict[K]

        This only concerns future differences.
        """
        data_prev = self.get().copy()

        async for data in self:
            outer_join = {
                k: (data_prev.get(k), data.get(k))
                for k in data_prev.keys() | data.keys()
            }
            for k, (u, v) in outer_join.items():
                if u is not None and v is not None:
                    key_fn = self[k]._key
                    ku, kv = key_fn(u), key_fn(v)
                    if ku is kv or ku == kv:
                        continue

                assert (
                    u is None and v is not None
                    or u is not None and v is None
                    or u is not None and v is not None
                )

                yield k, u, v

            data_prev = data

    async def additions(self) -> AIterator[tuple[_K, _S]]:
        """Returns an async iterator that yields tuples of (key, value) items
        that will be set to a new value."""
        async for k, u, v in self.differences():
            if u is None:
                assert v is not None
                yield k, v

    async def changes(self) -> AIterator[tuple[_K, _S, _S]]:
        """Returns an async iterator that yields tuples of
        (key, value_old, value_new).
        """
        async for k, u, v in self.differences():
            if u is not None and v is not None:
                yield k, u, v

    async def deletions(self) -> AIterator[tuple[_K, _S]]:
        """Returns an async iterator that yields tuples of (key, value) items
        that will be deleted.
        """
        async for k, u, v in self.differences():
            if v is None:
                assert u is not None
                yield k, u

    async def keys_contained(self) -> AIterator[tuple[_K, bool]]:
        """Returns an async iterator of (key, key in self) for the current and
        future keys.
        """
        for k in self:
            yield k, True

        async for k, u, v in self.differences():
            if u is None or v is None:
                yield k, k in self

    @overload
    def get(self, key: NothingType = ..., /) -> dict[_K, _S]: ...
    @overload
    def get(self, key: _K, /) -> _S: ...
    @overload
    def get(self, key: _K, /, default: _T = ...) -> _S | _T: ...

    def get(
        self,
        key: Maybe[_K] = Nothing,
        /,
        default: Maybe[_T] = Nothing
    ) -> dict[_K, _S] | _S | _T:
        if key is Nothing:
            if default is not Nothing:
                raise TypeError('default cannot be set if no key is passed')

            return self._get_data()

        return super().get(key, default=default)

    def update(
        self,
        arg: Maybe[Mapping[_K, _S]] = Nothing,
        /,
        **kwargs: _S
    ):
        """Analogous to dict.update().

        Raises StateError if an async iterable (producer) is setting this
        state.
        """
        self._ensure_mutable()

        if arg is Nothing:
            arg = {}
        elif isinstance(arg, StateDict):
            raise NotImplementedError()

        if isinstance(arg, Mapping):
            for k, v in arg.items():
                self[k] = v
        else:
            raise TypeError(f'mapping expected, got {type(arg).__name__!r}')

        if kwargs:
            for k, v in cast(dict[_K, _S], kwargs).items():
                self[k] = v

    def clear(self) -> None:
        """Analogous to dict.clear().

        Raises StateError if an async iterable (producer) is setting this
        state.
        """
        self._ensure_mutable()

        for key, state in self._states.items():
            state._collections.remove((key, self))

        self._states.clear()

    def _get_states(self, is_set: bool = True) -> dict[_K, StateVar[_S]]:
        if is_set:
            return {k: s for k, s in self._states.items() if s.is_set}
        else:
            return self._states

    def _get_data(self, default: Maybe[_T] = Nothing) -> dict[_K, _S | _T]:
        if default is Nothing:
            return {k: s._get() for k, s in self._states.items() if s.is_set}

        # noinspection PyProtectedMember
        return {k: s._get(default) for k, s in self._states.items()}

    def _set_item(
        self,
        item: tuple[_K, StateVar[_S] | _S | None] | Mapping[_K, _S]
    ):
        if isinstance(item, tuple):
            if len(item) != 2:
                raise TypeError(
                    f'{type(self).__name__} async iterable must yield tuples '
                    f'of length 2, got {repr(item)}'
                )

            items = [item]
        elif isinstance(item, Mapping):
            items = []
            for k, v in item.items():
                items.append((k, v))
            for k in (self.keys() - item.keys()):
                items.append((k, None))
        else:
            raise TypeError(item)

        for key, value in items:
            if value is None:
                self._states[key]._clear()

            elif isinstance(value, StateVar):
                if key in self._states:
                    state = self._states[key]

                    if state.is_set:
                        raise KeyError(f'{key!r} already set: {state}')

                    state.set_from(value)

                else:
                    self._states[key] = value
                    value._collections.append((key, self))
            else:
                self[key].set(cast(_S, value))


_SS = TypeVar('_SS', bound=State)


@overload
def statefunction(
    function: Callable[..., Awaitable[_RS]] | Callable[..., _RS],
) -> Callable[..., StateVar[_RS]]:
    ...


@overload
def statefunction(
    function: Callable[..., Awaitable[_RS]] | Callable[..., _RS],
    cls: type[_SS] = ...,
    *cls_args: Any,
    **cls_kwargs: Any,
) -> Callable[..., _SS]:
    ...


def statefunction(
    function: Callable[..., Awaitable[_RS]] | Callable[..., _RS],
    cls: type[State] = StateVar,
    *cls_args: Any,
    **cls_kwargs: Any,
) -> Callable[..., State[_RS]]:

    @functools.wraps(function)
    def res(*args: State[_S]) -> State[_RS]:
        if len(args) == 0:
            raise TypeError('at least one argument expected')

        if len(args) == 1:
            return args[0].map(function, cls, *cls_args, **cls_kwargs)

        return StateTuple(args).starmap(function, cls, *cls_args, **cls_kwargs)

    hints = get_type_hints(getattr(function, '__call__', function))
    origin: type[State] = get_origin(cls) or cls

    res.__annotations__ = {
        k: State[v] for k, v in hints.items() if k != 'return'
    }
    if 'return' in hints:
        return_hint = origin[hints['return']]  # type: ignore
    else:
        return_hint = origin

    res.__annotations__['return'] = return_hint

    return res
