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
        # noinspection PyAsyncCall
        Promise.reject(asyncio.CancelledError('spam'))


async def test_then():
    fut1 = asyncio_utils.create_future()
    fut2 = asyncio_utils.create_future()

    p1 = Promise(fut1)

    async def _then(_res1):
        _res2 = await fut2
        return _res1, _res2

    p2 = p1.then(_then)

    assert p1._state == 'pending'
    assert p2._state == 'pending'

    fut1.set_result('spam')
    await p1

    assert p1._state == 'fulfilled'
    assert p2._state == 'pending'

    fut2.set_result('ham')
    await p2

    assert p1._state == 'fulfilled'
    assert p2._state == 'fulfilled'

    res1 = await p1
    res2 = await p2
    assert res1 == 'spam'
    assert res2 == (res1, 'ham')


async def test_catch():
    fut = asyncio_utils.create_future()

    async def _then(_):
        return 'fail'

    async def _catch(exc):
        if isinstance(exc, ValueError):
            return exc.args[0] if exc.args else None
        raise

    p = Promise(fut).then(_then).catch(_catch)

    assert p._state == 'pending'
    fut.set_exception(ValueError('eggs'))

    res = await p
    assert res == 'eggs'


async def test_finally():
    fut_resolve = asyncio_utils.create_future()
    fut_reject = asyncio_utils.create_future()

    calls = []

    async def finally_():
        calls.append(True)

    p_resolve = Promise(fut_resolve).finally_(finally_)
    p_reject = Promise(fut_reject).finally_(finally_)

    assert p_resolve._state == 'pending'
    assert len(calls) == 0

    fut_resolve.set_result(42)
    res = await p_resolve

    assert res == 42
    assert len(calls) == 1

    assert p_reject._state == 'pending'

    fut_reject.set_exception(ZeroDivisionError)
    with pytest.raises(ZeroDivisionError):
        await p_reject

    assert len(calls) == 2
