import asyncio
from typing import Final

import pytest

from asynced import StateVar, StateVarTuple


DT: Final[float] = 0.01


# @pytest.fixture(scope='function', autouse=True)
# def timeout_1s():
#     return 1.0


async def test_manual_initial():
    s0, s1 = StateVar(), StateVar()
    s = StateVarTuple((s0, s1))

    assert not bool(s.is_set)
    assert not bool(s.is_done)
    assert not bool(s.is_stopped)
    assert not bool(s.is_error)
    assert not bool(s.is_cancelled)

    o = 'O'
    assert s.get(o) == (o, o)

    with pytest.raises(LookupError):
        s.get()

    ss = asyncio.shield(s)
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(ss, DT)
    ss.cancel()

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(s.next(), DT)


async def test_manual_sequence():
    s = StateVarTuple(3)

    assert len(s) == 3

    assert s[0] is not s[1]
    assert s[0] is not s[2]
    assert s[1] is not s[2]

    assert not s[0].readonly

    s0, s1, s2 = s
    assert s0 is s[0]
    assert s1 is s[1]
    assert s2 is s[2]
    assert s2 is s[-1]

    s12 = s[1:]
    assert isinstance(s12, StateVarTuple)
    assert len(s12) == 2
    assert s12[0] == s[1]
    assert s12[1] == s[2]

    r = reversed(s)
    assert isinstance(r, StateVarTuple)

    assert r[0] == s[2]
    assert r[1] == s[1]
    assert r[2] == s[0]


async def test_manual_set_single():
    s = StateVarTuple(1)

    s[0].set('spam')
    assert await s == ('spam', )
    await asyncio.sleep(DT / 2)

    assert bool(s.is_set)
    assert bool(s.all_set)

    assert not bool(s.is_done)
    assert not bool(s.is_stopped)
    assert not bool(s.is_error)
    assert not bool(s.is_cancelled)


