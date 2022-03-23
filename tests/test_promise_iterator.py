from __future__ import annotations

import asyncio
from typing import (
    AsyncIterator,
    AsyncGenerator,
    Final,
    Generic,
    Sequence,
    TypeVar,
)

import pytest

# noinspection PyProtectedMember
from asynced._compat import aiter, anext
from asynced import Promise, PromiseIterator


DELAY: Final[float] = .01


_T = TypeVar('_T')
_T_co = TypeVar('_T_co', covariant=True)


# noinspection PyPep8Naming
class _aiter_seq(AsyncIterator[_T_co], Generic[_T_co]):
    def __init__(self, seq: Sequence[_T_co], delay=DELAY):
        self.seq = seq
        self.delay = delay

        self.i = -1

    def __aiter__(self) -> AsyncIterator[_T_co]:
        return type(self)(self.seq, self.delay)

    async def __anext__(self) -> _T_co:
        self.i += 1

        if self.i >= len(self.seq):
            raise StopAsyncIteration

        return await asyncio.sleep(self.delay, self.seq[self.i])


async def _agen_seq(seq: Sequence[_T], delay=DELAY) -> AsyncGenerator[None, _T]:
    for i in seq:
        yield await asyncio.sleep(delay, i)


async def test_empty_loop():
    pit = PromiseIterator(_aiter_seq([]))

    assert not [i async for i in pit]


async def test_empty_anext():
    pit = PromiseIterator(_aiter_seq([]))

    with pytest.raises(StopAsyncIteration):
        await anext(pit)

    with pytest.raises(StopAsyncIteration):
        await anext(pit)


async def test_single():
    pit = PromiseIterator(_aiter_seq([42]))

    res = [i async for i in pit]
    assert len(res) == 1
    assert res[0] == 42

    res2 = [i async for i in pit]
    assert res2 == res


async def test_anext_promise():
    pit = PromiseIterator(_aiter_seq([42]))
    pi = anext(pit)
    p = await pi

    assert isinstance(pi, Promise)
    assert p == 42


async def test_aiterator_entanglement():
    seq = [42, 666]
    ait = _aiter_seq(seq)
    pit = PromiseIterator(ait)

    ai = anext(ait)
    pi = anext(pit)

    a = await ai
    p = await pi

    assert a == seq[0]
    assert p == seq[1]

    pit2 = aiter(pit)
    assert pit is not pit2

    p2 = await anext(pit2)

    assert a == seq[0]
    assert p == seq[1]
    assert p2 == seq[0]
