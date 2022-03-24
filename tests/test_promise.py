import pytest

from asynced import Promise, PromiseFuture
import asyncio

from asynced._aio_utils import create_future


async def test_initial():
    pfut = PromiseFuture()

    assert not pfut.done()
    with pytest.raises(asyncio.InvalidStateError):
        pfut.result()


async def test_resolve():
    p = Promise(asyncio.sleep(.01, 'spam'))

    res = await p
    assert res == 'spam'

    assert p.fulfilled()
    assert p.result() == 'spam'


async def test_reject():
    async def raiser():
        raise await asyncio.sleep(0, ZeroDivisionError)

    p = Promise(raiser())

    with pytest.raises(ZeroDivisionError):
        await p
    with pytest.raises(ZeroDivisionError):
        p.result()

    assert p.rejected()


async def test_cancel():
    pfut = PromiseFuture()
    pfut.cancel()

    with pytest.raises(asyncio.CancelledError):
        await pfut
    with pytest.raises(asyncio.CancelledError):
        pfut.result()

    assert pfut.cancelled()


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


async def test_then():
    p1 = PromiseFuture()

    fut = create_future()

    async def _mapper(_res1):
        _res2 = await fut
        return _res1, _res2

    p2 = p1.then(_mapper)

    assert not p1.done()
    assert not p2.done()

    p1.fulfill('spam')
    await p1

    assert p1.done()
    assert p1.fulfilled()
    assert not p2.done()

    fut.set_result('ham')
    await p2

    assert p2.done()
    assert p2.fulfilled()

    res1 = await p1
    res2 = await p2
    assert res1 == 'spam'
    assert res2 == (res1, 'ham')


async def test_catch():
    def _then(_):
        return 'fail'

    def _catch_value_error(exc):
        return exc.args[0] if exc.args else None

    p = PromiseFuture()
    p_res = p.then(_then).catch(ValueError, _catch_value_error)

    assert not p.done()
    assert not p_res.done()

    p.reject(ValueError('eggs'))

    with pytest.raises(ValueError):
        await p

    res = await p_res

    assert res == 'eggs'
    assert p_res.result() == 'eggs'
