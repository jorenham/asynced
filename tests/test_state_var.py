import asyncio
from typing import Final

import pytest

from asynced import StateVar
from asynced.compat import anext, aiter


DT: Final[float] = .01

#
# @pytest.fixture(scope='function', autouse=True)
# def timeout_1s():
#     return 1.0


async def slowrange(dt, *args):
    for i in range(*args):
        await asyncio.sleep(dt)
        yield i


async def state_enumerate(statevar: StateVar):
    i = 0
    async for v in statevar:
        yield i, v
        i += 1


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

    s.set('ham')
    assert await s == 'ham'
    assert s.get() == 'ham'


async def test_manual_aiter():
    s = StateVar()
    ss = aiter(s)

    assert s.set('spam')
    assert await s == 'spam'
    assert await anext(ss) == 'spam'

    assert s.set('ham')
    assert await s == 'ham'
    assert await anext(ss) == 'ham'


async def test_manual_dedupe():
    s = StateVar()
    r = StateVar(state_enumerate(s))

    assert s.set('spam')

    ri, rv = await r
    assert ri == 0
    assert rv == 'spam'

    rnext = anext(r)
    assert s.set('ham')
    ri, rv = await rnext

    assert ri == 1
    assert rv == 'ham'

    assert not s.set('ham')

    o1 = object()
    o2 = object()
    assert o1 is o1
    assert o1 is not o2
    assert o1 != o2

    rnext = anext(r)
    assert s.set(o1)
    ri, rv = await rnext

    assert ri == 2
    assert rv is o1

    rnext = anext(r)

    assert not s.set(o1)
    assert s.set(o2)

    ri, rv = await rnext

    assert ri == 3
    assert rv is o2


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

    assert await anext(s) == 42
    assert await anext(s) == 43
    assert await anext(s) == 44


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

    s._consumer.cancel()

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


async def test_initial_map():
    s = StateVar()
    s.set(21)
    s2 = s.map(lambda x: x * 2)

    assert s.is_set
    assert s2.is_set

    assert s.get() == 21
    assert s2.get() == 42


async def test_iterable_empty():
    s = StateVar(slowrange(DT, 0))
    s_list = [x async for x in s]
    assert len(s_list) == 0


async def test_iterable_dedupe():
    o1 = object()
    o2 = object()

    async def produper():
        yield 'spam'

        await asyncio.sleep(DT)
        yield 'ham'
        await asyncio.sleep(DT)
        yield 'ham'

        await asyncio.sleep(DT)
        yield o1
        await asyncio.sleep(DT)
        yield o1

        await asyncio.sleep(DT)
        yield o2

    ss = [v async for v in StateVar(produper())]
    assert ss[0] == 'spam'
    assert ss[1] == 'ham'
    assert ss[2] is o1
    assert ss[3] is o2
