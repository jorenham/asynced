import asyncio

import pytest

from asynced import asyncio_utils, Promise


async def _raiser(coro):
    raise await coro


async def test_initial():
    fut = asyncio_utils.create_future()
    p = Promise(fut)

    task = asyncio.create_task(p)
    assert not task.done()

    assert p._state == 'pending'


async def test_resolve():
    p = Promise(asyncio.sleep(.01, 'spam'))

    res = await p
    assert res == 'spam'

    assert p._state == 'fulfilled'


async def test_reject():
    p = Promise(_raiser(asyncio.sleep(.01, ZeroDivisionError)))

    with pytest.raises(ZeroDivisionError):
        await p

    assert p._state == 'rejected'


async def test_cancel():
    fut = asyncio_utils.create_future()
    p = Promise(fut)
    fut.cancel()

    with pytest.raises(asyncio.CancelledError):
        await p

    assert p._state == 'cancelled'
