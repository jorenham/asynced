import anyio
import time
from typing import Final

import pytest

from asynced import StateVar, StateTuple
from asynced.compat import aiter, anext


pytestmark = pytest.mark.anyio


DT: Final[float] = 0.01


async def test_initial():
    s0, s1 = StateVar(), StateVar()
    s = StateTuple((s0, s1))

    assert not s.is_set
    assert not s.is_done
    assert not s.is_stopped

    assert not s.all_error
    assert not s.any_error
    assert not s.is_error

    assert not s.is_cancelled

    o = 'O'
    assert s.get(0, o) == s.get(1, o) == o

    with pytest.raises(LookupError):
        s.get(0)
    with pytest.raises(LookupError):
        s.get(1)
    with pytest.raises(IndexError):
        s.get(2)


async def test_indexing():
    s = StateTuple(3)

    assert len(s) == 3

    s_first = s[0]
    assert isinstance(s_first, StateVar)
    assert not s_first.readonly

    s_last = s[-1]
    assert isinstance(s_last, StateVar)
    assert s_last is not s_first
    assert s_last.name is not s_first.name


async def test_unpacking():
    s = StateTuple(2)

    s0, s1 = s

    assert s0 is not s1

    assert isinstance(s0, StateVar)
    assert s0 is s[0]
    assert s1 is s[1]


async def test_slicing():
    s = StateTuple(3)

    s_copy = s[:]
    assert isinstance(s_copy, StateTuple)
    assert s_copy is not s
    assert all(a is b for a, b in zip(s, s_copy))

    s12 = s[1:]
    assert s12[0] is s[1]
    assert s12[1] is s[2]


async def test_reversed():
    s = StateTuple(3)

    r = reversed(s)
    assert isinstance(r, StateTuple)

    assert r[0] is s[2]
    assert r[1] is s[1]
    assert r[2] is s[0]


async def test_set_single():
    s = StateTuple(1)

    s[0] = 'spam'

    assert (await s) == ('spam',)
    assert (await s[0]) == 'spam'
    assert s[0].get() == 'spam'

    assert s.get(0) == 'spam'

    assert s.any_set
    assert s.all_set


async def test_set_multi():
    s = StateTuple(3)

    s[0] = 'spam'

    assert (await s[0]) == 'spam'
    assert s[0].get() == 'spam'
    assert s[0].is_set

    assert s.any_set
    assert not s.all_set

    assert s.get(0) == 'spam'

    assert s.get(1, None) is None
    assert s.get(2, None) is None

    with pytest.raises(LookupError):
        s.get(1)
    with pytest.raises(LookupError):
        s.get(2)

    s[1] = 'ham'

    assert bool(s.any_set)
    assert not bool(s.is_set)
    assert s.get(1) == 'ham'

    with pytest.raises(LookupError):
        s.get(2)

    s[2] = 'eggs'

    await s
    assert bool(s.any_set)
    assert bool(s.is_set)

    assert s.get(2) == 'eggs'
    assert await s == ('spam', 'ham', 'eggs')


async def test_set_together():
    s = StateTuple(1) * 2
    assert len(s) == 2
    assert s[0] is s[1]

    s[0] = 'spam'
    await s
    assert s.is_set
    assert s.get(0) == 'spam'
    assert s.get(1) == 'spam'


async def test_producers_single_empty():
    class EmptyAiter:
        def __aiter__(self):
            return self

        def __anext__(self):
            raise StopAsyncIteration

    s = StateTuple([EmptyAiter()])
    ss = [v async for v, in s]
    assert not len(ss)

    assert not s.is_set
    assert not s.all_set
    assert not s.any_set

    assert s.any_done
    assert s.all_done
    assert s.is_done

    assert not s.is_error
    assert not s.all_error
    assert not s.any_error

    assert not s.is_cancelled
    assert not s.all_cancelled
    assert not s.any_cancelled


async def test_producers_single_stop():
    async def producer():
        yield 'spam'
        await anyio.sleep(DT)
        yield 'ham'
        await anyio.sleep(DT)
        yield 'eggs'
        await anyio.sleep(DT)

    s = StateTuple[str]([producer()])

    assert len(s) == 1
    assert not s.any_set
    assert not s.all_set
    assert not s.any_done
    assert not s.all_done

    ss = [v async for v, in s]

    assert ss == ['spam', 'ham', 'eggs']

    assert s[0].is_set
    assert s.is_set
    assert s.any_set
    assert s.all_set

    assert s[0].is_done
    assert s.is_done
    assert s.any_done
    assert s.all_done

    assert not s[0].is_error
    assert not s.is_error
    assert not s.any_error
    assert not s.all_error


async def test_producers_single_error():
    async def producer():
        await anyio.sleep(DT)
        yield 1 / 0

    s = StateTuple([producer()])

    with pytest.raises(ZeroDivisionError):
        await s

    assert s[0].is_error
    assert s.is_error
    assert s.any_error
    assert s.all_error

    assert s.is_done
    assert not s.is_set

    assert not s.is_cancelled

    with pytest.raises(ZeroDivisionError):
        await s


async def test_producers_single_set_and_error():
    async def producer():
        await anyio.sleep(DT)
        yield 1 / 1
        await anyio.sleep(DT)
        yield 1 / 0

    s = StateTuple([producer()])
    ss = aiter(s)

    assert await anext(ss) == (1, )
    with pytest.raises(ZeroDivisionError):
        assert await anext(ss) == -1/12

    with pytest.raises(ZeroDivisionError):
        await s

    with pytest.raises(ZeroDivisionError):
        await s[0]
    #
    # assert s[0].is_set
    # assert s.is_set
    assert s.any_error
    assert s.is_error
    assert s.is_done
    # assert not s.is_stopped
    assert not s.is_cancelled

    with pytest.raises(ZeroDivisionError):
        await s


async def test_producers_interleaved_stop():
    async def eggs():
        yield 'eggs'
        await anyio.sleep(DT * 2)
        yield 'EGGS'
        await anyio.sleep(DT * 2)

    async def bacon():
        await anyio.sleep(DT)
        yield 'bacon'
        await anyio.sleep(DT * 2)
        yield 'BACON'
        await anyio.sleep(DT)

    s = StateTuple([eggs(), bacon()])

    t0 = time.monotonic()
    ss = [v async for v in s]
    t = time.monotonic() - t0

    assert len(ss) == 3
    assert ss[0] == ('eggs', 'bacon')
    assert ss[1] == ('EGGS', 'bacon')
    assert ss[2] == ('EGGS', 'BACON')

    assert t >= DT * 4
    assert t < DT * 8

    assert s.is_set
    assert s.is_done
    assert s.is_stopped
    assert not s.is_error
    assert not s.is_cancelled


async def test_producers_interleaved_error():
    async def producer0():
        yield 1 / 1
        await anyio.sleep(DT*2)
        yield 0 / 1
        await anyio.sleep(DT*2)
        yield -1 / 1
        await anyio.sleep(DT)

    async def producer1():
        await anyio.sleep(DT)
        yield 1 / 1
        await anyio.sleep(DT*2)
        yield 1 / 0
        await anyio.sleep(DT*2)
        yield 1 / -1

    s = StateTuple([producer0(), producer1()])
    ss = aiter(s)

    assert await anext(ss) == (1, 1)
    assert s.is_set
    assert not s.is_error

    assert await anext(ss) == (0, 1)

    with pytest.raises(ZeroDivisionError):
        assert await anext(ss) == (0, -1/12)
    with pytest.raises(StopAsyncIteration):
        assert await anext(ss) == (-1, -1/12)
    with pytest.raises(StopAsyncIteration):
        assert await anext(ss) == (-1, -1)

    await anyio.sleep(DT * 4)

    assert s.is_done
    assert s[0].is_done
    assert s[1].is_done

    assert not s[0].is_error
    assert s[1].is_error

    assert s.any_error
    assert s.is_error

    assert s[0].is_stopped
    assert not s[1].is_stopped

    assert s.any_stopped
    assert not s.all_stopped
    assert s.is_stopped
