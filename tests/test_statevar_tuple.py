import asyncio
import time
from typing import Final

import pytest

from asynced import StateVar, StateVarTuple
from asynced.compat import aiter, anext


DT: Final[float] = 0.01

@pytest.fixture(scope='function', autouse=True)
def timeout_1s():
    return 1.0


async def test_initial():
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


async def test_indexing():
    s = StateVarTuple(3)

    assert len(s) == 3

    s_first = s[0]
    assert isinstance(s_first, StateVar)
    assert not s_first.readonly

    s_last = s[-1]
    assert isinstance(s_last, StateVar)
    assert s_last is not s_first
    assert s_last.name is not s_first.name


async def test_unpacking():
    s = StateVarTuple(2)

    s0, s1 = s

    assert s0 is not s1

    assert isinstance(s0, StateVar)
    assert s0 is s[0]
    assert s1 is s[1]


async def test_slicing():
    s = StateVarTuple(3)

    s_copy = s[:]
    assert isinstance(s_copy, StateVarTuple)
    assert s_copy is not s
    assert all(a is b for a, b in zip(s, s_copy))

    s12 = s[1:]
    assert s12[0] is s[1]
    assert s12[1] is s[2]


async def test_reversed():
    s = StateVarTuple(3)

    r = reversed(s)
    assert isinstance(r, StateVarTuple)

    assert r[0] is s[2]
    assert r[1] is s[1]
    assert r[2] is s[0]


async def test_set_single():
    s = StateVarTuple(1)

    s[0] = 'spam'

    assert (await s[0]) == 'spam'
    assert s[0].get() == 'spam'

    assert s.get() == ('spam', )
    assert (await s) == ('spam',)

    assert bool(s.is_set)
    assert bool(s.all_set)


async def test_set_multi():
    s = StateVarTuple(3)

    s[0] = 'spam'

    assert (await s[0]) == 'spam'
    assert s[0].get() == 'spam'
    assert s[0].is_set

    assert bool(s.is_set)
    assert not bool(s.all_set)
    assert s.get(None) == ('spam', None, None)
    with pytest.raises(LookupError):
        s.get()

    s[1] = 'ham'

    assert bool(s.is_set)
    assert not bool(s.all_set)
    assert s.get(None) == ('spam', 'ham', None)
    with pytest.raises(LookupError):
        s.get()

    s[2] = 'eggs'
    assert bool(s.is_set)
    assert bool(s.all_set)
    assert s.get() == ('spam', 'ham', 'eggs')
    assert await s == ('spam', 'ham', 'eggs')


async def test_set_together():
    s = StateVarTuple(1) * 2
    assert len(s) == 2
    assert s[0] is s[1]

    s[0] = 'spam'
    assert s.all_set
    assert s.get() == ('spam', ) * 2


async def test_producers_single_empty():
    class EmptyAiter:
        def __aiter__(self):
            return self

        def __anext__(self):
            raise StopAsyncIteration

    s = StateVarTuple([EmptyAiter()])
    ss = [v async for v, in s]
    assert not len(ss)

    assert not s.is_set
    assert not s.all_set
    assert s.all_done
    assert s.all_stopped
    assert not s.all_error
    assert not s.all_cancelled


async def test_producers_single_stop():
    async def producer():
        yield 'spam'
        await asyncio.sleep(DT)
        yield 'ham'
        await asyncio.sleep(DT)
        yield 'eggs'
        await asyncio.sleep(DT)

    s = StateVarTuple([producer()])
    ss = [v async for v, in s]

    assert len(ss) == 3
    assert ss == ['spam', 'ham', 'eggs']

    assert s.all_set
    assert s.all_done
    assert s.all_stopped
    assert not s.all_error
    assert not s.all_cancelled


async def test_producers_single_error():
    async def producer():
        await asyncio.sleep(DT)
        yield 1 / 0

    s = StateVarTuple([producer()])

    with pytest.raises(ZeroDivisionError):
        await s

    assert s.is_error
    assert s.all_error
    assert s.all_done
    assert not s.all_set
    assert not s.all_stopped
    assert not s.all_cancelled

    with pytest.raises(ZeroDivisionError):
        await s


async def test_producers_single_set_and_error():
    async def producer():
        await asyncio.sleep(DT)
        yield 1 / 1
        await asyncio.sleep(DT)
        yield 1 / 0

    s = StateVarTuple([producer()])
    ss = aiter(s)

    assert await anext(ss) == (1, )
    with pytest.raises(ZeroDivisionError):
        assert await anext(ss) == -1/12

    assert s.all_set
    assert s.is_error
    assert s.all_error
    assert s.all_done
    assert not s.all_stopped
    assert not s.all_cancelled

    with pytest.raises(ZeroDivisionError):
        await s


async def test_producers_interleaved_stop():
    async def eggs():
        yield 'eggs'
        await asyncio.sleep(DT)
        yield 'EGGS'
        await asyncio.sleep(DT)

    async def bacon():
        await asyncio.sleep(DT)
        yield 'bacon'
        await asyncio.sleep(DT)
        yield 'BACON'

    s = StateVarTuple([eggs(), bacon()])

    t0 = time.monotonic()
    ss = [v async for v in s]
    t = time.monotonic() - t0

    assert len(ss) == 3
    assert ss[0] == ('eggs', 'bacon')
    assert ss[1] == ('EGGS', 'bacon')
    assert ss[2] == ('EGGS', 'BACON')

    assert t >= DT * 2
    assert t < DT * 4

    assert s.all_set
    assert s.all_done
    assert s.all_stopped
    assert not s.all_error
    assert not s.all_cancelled


async def test_producers_interleaved_error():
    async def producer0():
        yield 1 / 1
        await asyncio.sleep(DT)
        yield 0 / 1
        await asyncio.sleep(DT)
        yield -1 / 1

    async def producer1():
        await asyncio.sleep(DT)
        yield 1 / 1
        await asyncio.sleep(DT)
        yield 1 / 0
        await asyncio.sleep(DT)
        yield 1 / -1

    s = StateVarTuple([producer0(), producer1()])
    ss = aiter(s)

    assert await anext(ss) == (1, 1)
    assert s.all_set
    assert not s.is_error

    assert await anext(ss) == (0, 1)

    with pytest.raises(ZeroDivisionError):
        assert await anext(ss) == (0, -1/12)
    with pytest.raises(ZeroDivisionError):
        assert await anext(ss) == (-1, -1/12)
    with pytest.raises(ZeroDivisionError):
        assert await anext(ss) == (-1, -1)

    assert s.all_done

    assert not s.is_error
    assert not s.all_error

    assert not s.is_cancelled
    assert not s.all_cancelled

    assert s.is_stopped
    assert not s.all_stopped
