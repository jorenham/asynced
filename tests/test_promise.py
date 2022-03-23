import pytest

from asynced import Promise
import asyncio


def future() -> asyncio.Future:
    return asyncio.get_event_loop().create_future()


def future_promise() -> tuple[asyncio.Future, Promise]:
    fut = future()
    return fut, Promise(fut)


async def test_initial():
    fut, p = future_promise()

    task = asyncio.create_task(p)

    assert not task.done()
    assert p.is_pending


async def test_resolve():
    p = Promise(asyncio.sleep(.01, 'spam'))

    res = await p
    assert res == 'spam'

    assert p.is_fulfilled


async def test_reject():
    async def raiser():
        raise await asyncio.sleep(0, ZeroDivisionError)

    p = Promise(raiser())

    with pytest.raises(ZeroDivisionError):
        await p

    assert p.is_rejected


async def test_cancel():
    fut, p = future_promise()
    fut.cancel()

    with pytest.raises(asyncio.CancelledError):
        await p

    assert p.is_cancelled


async def test_already_resolved():
    p = Promise.as_fulfilled(42)
    res = await p
    assert res == 42


async def test_already_rejected_exception():
    p = Promise.as_rejected(ZeroDivisionError())
    with pytest.raises(ZeroDivisionError):
        await p


async def test_already_rejected_exception_type():
    p = Promise.as_rejected(ZeroDivisionError)
    with pytest.raises(ZeroDivisionError):
        await p


async def test_already_rejected_no_exception():
    with pytest.raises(TypeError):
        Promise.as_rejected('spam')


async def test_already_rejected_base_exception():
    with pytest.raises(TypeError):
        # noinspection PyAsyncCall
        Promise.as_rejected(asyncio.CancelledError('spam'))


async def test_amap():
    fut1, p1 = future_promise()
    fut2 = future()

    async def _mapper(_res1):
        _res2 = await fut2
        return _res1, _res2

    p2 = p1.amap(_mapper)

    assert p1.is_pending
    assert p2.is_pending

    fut1.set_result('spam')
    await p1

    assert p1.is_fulfilled
    assert p2.is_pending

    fut2.set_result('ham')
    await p2

    assert p1.is_fulfilled
    assert p2.is_fulfilled

    res1 = await p1
    res2 = await p2
    assert res1 == 'spam'
    assert res2 == (res1, 'ham')


async def test_catch():
    def _then(_):
        return 'fail'

    def _catch_value_error(exc):
        return exc.args[0] if exc.args else None

    fut = future()
    p = Promise(fut).map(_then).catch(ValueError, _catch_value_error)

    assert p.is_pending
    fut.set_exception(ValueError('eggs'))

    res = await p
    assert res == 'eggs'
