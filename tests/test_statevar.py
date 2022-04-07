import asyncio
from typing import Final

import pytest

from asynced import StateError, StateVar
from asynced.compat import anext


DT: Final[float] = .01


async def slowrange(dt, *args):
    for i in range(*args):
        await asyncio.sleep(dt)
        yield i


async def test_manual_initial():
    s = StateVar()

    assert not s.readonly

    assert not bool(s.is_done)
    assert not bool(s.is_set)
    assert not bool(s.is_stopped)
    assert not bool(s.is_error)
    assert not bool(s.is_cancelled)

    o = object()
    assert s.get(o) is o

    with pytest.raises(LookupError):
        s.get()

    ss = asyncio.shield(s)
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(ss, DT)
    ss.cancel()

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(s.next(), DT)


async def test_manual_set():
    s = StateVar()
    s.set('spam')

    assert await s == 'spam'
    assert s.get(object()) == 'spam'
    assert s.get() == 'spam'

    assert bool(s.is_set)
    assert not bool(s.is_done)
    assert not bool(s.is_stopped)
    assert not bool(s.is_error)
    assert not bool(s.is_cancelled)


async def test_manual_next():
    s = StateVar()
    sn = s.next()

    assert not bool(sn.is_done)
    assert not bool(sn.is_set)
    assert not bool(sn.is_stopped)
    assert not bool(sn.is_error)
    assert not bool(sn.is_cancelled)

    s.set('spam')

    assert bool(sn.is_done)
    assert bool(sn.is_set)
    assert not bool(sn.is_stopped)
    assert not bool(sn.is_error)
    assert not bool(sn.is_cancelled)

    assert await sn == 'spam'


async def test_iterable_initial():
    s = StateVar(slowrange(DT, 1))

    assert not bool(s.is_done)
    assert not bool(s.is_set)
    assert not bool(s.is_stopped)
    assert not bool(s.is_error)
    assert not bool(s.is_cancelled)

    o = object()
    assert s.get(o) is o

    with pytest.raises(LookupError):
        s.get()


async def test_iterable_set():
    s = StateVar(slowrange(DT, 42, 44))

    assert await s == 42
    assert s.get(object()) == 42
    assert s.get() == 42

    assert bool(s.is_set)
    assert not bool(s.is_done)
    assert not bool(s.is_stopped)
    assert not bool(s.is_error)
    assert not bool(s.is_cancelled)

    await asyncio.sleep(DT * 1.1)
    assert await s == 43


async def test_iterable_next():
    s = StateVar(slowrange(DT, 42, 45))

    assert await s.next() == 42
    assert await s.next() == 43
    assert await s.next() == 44


async def test_iterable_exhaust():
    s = StateVar(slowrange(DT, 42, 44))

    res = [i async for i in s]
    assert res == [42, 43]

    assert bool(s.is_set)
    assert bool(s.is_done)
    assert bool(s.is_stopped)
    assert not bool(s.is_error)
    assert not bool(s.is_cancelled)


async def test_iterable_error():
    async def itexc():
        yield 'sugma'
        await asyncio.sleep(DT)
        yield 1 / 0

    s = StateVar(itexc())
    assert await s == 'sugma'

    with pytest.raises(ZeroDivisionError):
        await anext(s)

    assert bool(s.is_set)
    assert bool(s.is_done)
    assert not bool(s.is_stopped)
    assert bool(s.is_error)
    assert not bool(s.is_cancelled)


async def test_iterable_cancelled():
    async def itcancel():
        await asyncio.sleep(DT)
        yield 'ligma'
        await asyncio.sleep(DT * 10)
        assert False

    s = StateVar(itcancel())
    assert await anext(s) == 'ligma'

    await asyncio.sleep(DT)

    s.as_future().cancel()

    default = object()
    assert await anext(s, default) is default

    assert bool(s.is_set)
    assert bool(s.is_done)
    assert not bool(s.is_stopped)
    assert not bool(s.is_error)
    assert bool(s.is_cancelled)


async def test_iterable_map():
    s = StateVar(slowrange(DT, 1, 4))

    s2 = s.map(lambda x: x**2)

    s2_list = [x2 async for x2 in s2]
    assert len(s2_list) == 3
    assert s2_list == [1, 4, 9]


async def test_iterable_empty():
    s = StateVar(slowrange(DT, 0))
    s_list = [x async for x in s]
    assert len(s_list) == 0
