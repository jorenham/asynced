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
    assert not p


async def test_resolve():
    p = Promise(asyncio.sleep(.01, 'spam'))

    res = await p
    assert res == 'spam'

    assert p._state == 'fulfilled'
    assert p


async def test_reject():
    p = Promise(_raiser(asyncio.sleep(.01, ZeroDivisionError)))

    with pytest.raises(ZeroDivisionError):
        await p

    assert p._state == 'rejected'
    assert p


async def test_cancel():
    fut = asyncio_utils.create_future()
    p = Promise(fut)
    fut.cancel()

    with pytest.raises(asyncio.CancelledError):
        await p

    assert p._state == 'cancelled'
    assert p


async def test_already_resolved():
    p = Promise.resolve(42)
    res = await p
    assert res == 42


async def test_already_rejected_exception():
    p = Promise.reject(ZeroDivisionError())
    with pytest.raises(ZeroDivisionError):
        await p


async def test_already_rejected_exception_type():
    p = Promise.reject(ZeroDivisionError)
    with pytest.raises(ZeroDivisionError):
        await p


async def test_already_rejected_no_exception():
    with pytest.raises(TypeError):
        Promise.reject('spam')  # noqa


async def test_already_rejected_base_exception():
    with pytest.raises(TypeError):
        Promise.reject(asyncio.CancelledError('spam'))
