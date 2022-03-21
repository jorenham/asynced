import pytest

from asynced import Promise
import asyncio


async def _raiser(coro):
    raise await coro


def future() -> asyncio.Future:
    return asyncio.get_event_loop().create_future()


def future_promise() -> tuple[asyncio.Future, Promise]:
    fut = future()
    return fut, Promise(fut)


async def test_initial():
    fut, p = future_promise()

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
    fut, p = future_promise()
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
    fut1, p1 = future_promise()
    fut2 = future()

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
    async def _then(_):
        return 'fail'

    async def _catch(exc):
        if isinstance(exc, ValueError):
            return exc.args[0] if exc.args else None
        raise

    fut = future()
    p = Promise(fut).then(_then).catch(_catch)

    assert p._state == 'pending'
    fut.set_exception(ValueError('eggs'))

    res = await p
    assert res == 'eggs'


async def test_finally():
    fut_resolve, fut_reject = future(), future()

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
