import math
import operator
import anyio
import pytest

from asynced import statefunction, StateVar


pytestmark = pytest.mark.anyio


async def test_unary_statevar():
    slen = statefunction(len)
    s = StateVar()
    sl = slen(s)

    assert not sl.is_set

    s.set('spam')
    await anyio.sleep(0)

    assert sl.is_set
    assert await sl == 4
    assert await slen(s) == 4

    s.set('ham')
    await anyio.sleep(0)

    assert await sl == 3
    assert await slen(s) == 3


async def test_binary_statevar():
    get_c = statefunction(lambda _a, _b: math.sqrt(_a**2 + _b**2))

    a = StateVar()
    b = StateVar()
    c = get_c(a, b)

    assert not c.is_set

    a.set(3)
    await anyio.sleep(0)

    assert not c.is_set

    b.set(4)
    await anyio.sleep(0)

    assert await c == 5.0
    assert c.is_set


async def test_binary_initial():
    a = StateVar()
    b = StateVar()

    a.set(5)
    b.set(37)

    c = statefunction(operator.add)(a, b)

    assert a.is_set
    assert b.is_set
    assert c.is_set

    assert a.get() == 5
    assert b.get() == 37
    assert c.get() == 42
