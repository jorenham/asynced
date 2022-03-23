from __future__ import annotations

__all__ = (
    'Mappable',
    'Catchable',
)

import abc
from asyncio import get_event_loop
from typing import Any, Protocol, runtime_checkable, TypeVar, Union
from typing_extensions import Self

from ._aio_utils import wrap_async
from ._typing import AFunc1, Func1

_T = TypeVar('_T')
_T_co = TypeVar('_T_co', covariant=True)

_R = TypeVar('_R')
_E = TypeVar('_E', bound=BaseException)


@runtime_checkable
class Mappable(Protocol[_T_co]):
    """A generic type that can have its type argument transformed."""
    __slots__ = ()

    def map(self, __func: Func1[_T_co, _R], /) -> Self[_R]:
        """Create a new instance with the funciton mapped to the value."""
        return self.amap(wrap_async(__func))

    @abc.abstractmethod
    def amap(self, __func: AFunc1[_T_co, _R], /) -> Self[_R]: ...

    def watch(self, __callback: Func1[_T_co, Any], /) -> Self[_T_co]:
        """Like .map(), but the function argument is returned; its return
        value or raised exception are ignored."""
        return self.amap(wrap_async(__callback))

    def awatch(self, __acallback: AFunc1[_T_co, Any], /) -> Self[_T_co]:
        """Like .watch(), but for async callbacks."""
        loop = get_event_loop()

        def _map(arg: _T) -> _T:
            loop.create_task(__acallback(arg))
            return arg

        return self.map(_map)


@runtime_checkable
class Catchable(Protocol[_T_co]):
    """A generic type that can have its exception mapped."""
    __slots__ = ()

    def catch(
        self, __typ: type[_E] | tuple[type[_E], ...], __func: Func1[_E, _R], /
    ) -> Self[Union[_T_co, _R]]:
        """Create a new instance with the result of the function."""
        return self.acatch(__typ, wrap_async(__func))

    @abc.abstractmethod
    def acatch(
        self, __typ: type[_E] | tuple[type[_E], ...], __func: AFunc1[_E, _R], /
    ) -> Self[Union[_T_co, _R]]:
        ...
