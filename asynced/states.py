from __future__ import annotations

__all__ = (
    'StateVar',
    'StateTuple',
    'StateDict',
    'statefunction',
)


import asyncio
import functools
import itertools

from typing import (
    Any,
    AsyncIterable,
    AsyncIterator,
    Awaitable,
    Callable,
    cast, ClassVar,
    Final,
    Generic,
    get_origin,
    get_type_hints,
    Hashable,
    ItemsView,
    Iterable,
    KeysView,
    Mapping,
    overload,
    Sequence,
    TypeVar,
    Union,
    ValuesView,
)
from typing_extensions import TypeAlias

from . import StateError
from ._states import State, StateCollection
from ._typing import (
    Comparable,
    Maybe,
    Nothing,
    NothingType,
)
from .asyncio_utils import race


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
        producer: Maybe[AsyncIterable[_S]] = Nothing,
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
        self._check_next()

        if not (skip := self._equals(state)):
            self._set(state)

        return not skip

    def set_from(self, state_producer: AsyncIterable[_S]):
        self._check()
        self._set_from(state_producer)

    def split(self: StateVar[Sequence[_RS]]) -> StateTuple[_RS]:
        # TODO docstring
        n = len(self.get())

        async def producer(i: int):
            async for state in self:
                yield state[i]

        return StateTuple(map(producer, range(n)))

    def _get_task_name(self) -> str:
        return self._name

    def _on_set(self, state: Maybe[_S] = Nothing) -> None:
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


class StateTuple(
    StateCollection[int, _S, tuple[_S, ...]],
    Sequence[_S],
    Generic[_S],
):
    __slots__ = ('_states', )

    _states: tuple[StateVar[_S], ...]

    def __init__(
        self,
        producers: int | Iterable[AsyncIterable[_S]] | AsyncIterable[_S],
    ) -> None:
        super().__init__()

        if isinstance(producers, int):
            states = [StateVar() for _ in range(producers)]
        elif isinstance(producers, AsyncIterable):
            # TODO
            raise NotImplementedError
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
                    statevar = StateVar(cast(AsyncIterable[_S], producer))

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

        if not self.is_set and all(s.is_set for s in states):
            self._set(tuple(s.get() for s in states))

    def __iter__(self):
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

    @property
    def readonly(self) -> bool:
        """.set() cannot be used, but unless the statevar items are readable,
        they can be set with e.g. self[0] = ...
        """
        return True

    def get(self, index: int, default: Maybe[_T] = Nothing) -> _S | _T:
        # noinspection PyProtectedMember
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

    # PyCharm doesn't understand walrus precedence:
    # noinspection PyRedundantParentheses
    async def _get_producer(self) -> AsyncIterator[tuple[_S]]:
        try:
            yield (states := tuple(await asyncio.gather(*self._states)))

            async for i, state in race(*self._states):
                yield (states := states[:i] + (state, ) + states[i+1:])

        except StopAsyncIteration:
            pass

    def _get_states(self) -> Mapping[int, State[_S]]:
        return {i: s for i, s in enumerate(self._states)}

    def _get_data(self, default: Maybe[_S] = Nothing) -> tuple[_S, ...]:
        if default is Nothing:
            return tuple(sv.get() for sv in self._states)
        else:
            return tuple(sv.get(default) for sv in self._states)


class StateDict(
    StateCollection[_K, _S, dict[_K, _S]],
    Mapping[_K, State[_S]],
    Generic[_K, _S],
):
    __slots__ = ('_states',)

    _states: dict[_K, State[_S]]

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

        self._future.set_result({})

        self._states = {}
        for key, state in states.items():
            if not isinstance(state, State):
                raise TypeError(
                    f'expected a State or async iterables, got '
                    f'{type(state).__name__!r} instead'
                )

            state._collections.append((key, self))

            self._states[cast(_K, key)] = state

    def __bool__(self) -> bool:
        return len(self) > 0

    def __iter__(self):
        return iter(self._get_states())

    def __contains__(self, key: _K) -> bool:
        return key in self._states and self._states[key].is_set

    def __getitem__(self, key: _K) -> State[_S]:
        states = self._states
        if key in states:
            return states[key]

        return self.__missing__(key)

    def __setitem__(self, key: _K, value: State[_S] | _S) -> None:
        if self._producer is not Nothing:
            raise StateError(f'{self!r} is readonly')

        if isinstance(value, State):
            if key in self._states:
                state = self._states[key]

                if state.is_set:
                    raise KeyError(f'{key!r} already set: {state}')

                state._set_from(value)

            else:
                self._states[key] = value
                value._collections.append((key, self))
        else:
            self[key]._set(value)

    def __delitem__(self, key: _K) -> None:
        if self._producer is not Nothing:
            raise StateError(f'{self!r} is readonly')

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

        state = self._states[key] = StateVar()
        state._collections.append((key, self))

        return state

    def get(
        self,
        key: Maybe[_K] = Nothing,
        default: Maybe[_T] = Nothing
    ) -> _S | _T | dict[_K, _S]:
        if key is Nothing:
            if default is not Nothing:
                raise TypeError('default cannot be set if no key is passed')

            return self._get_data()

        return super().get(key, default=default)

    def keys(self, is_set: bool = True) -> KeysView[_K]:
        return self._get_states(is_set).keys()

    def values(self, is_set: bool = True) -> ValuesView[State[_S]]:
        return self._get_states(is_set).values()

    def items(self, is_set: bool = True) -> ItemsView[_K, State[_S]]:
        return self._get_states(is_set).items()

    def clear(self) -> None:
        states = self._states

        for key, state in states.items():
            state._collections.remove((key, self))

        self._states.clear()

    def _get_states(self, is_set: bool = True) -> dict[_K, State[_S]]:
        if is_set:
            return {k: s for k, s in self._states.items() if s.is_set}
        else:
            return self._states

    def _get_data(self, default: Maybe[_T] = Nothing) -> dict[_K, _S | _T]:
        if default is Nothing:
            return {k: s._get() for k, s in self._states.items() if s.is_set}

        # noinspection PyProtectedMember
        return {k: s._get(default) for k, s in self._states.items()}


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
