import asyncio

import pytest

from edpy._compat import *  # noqa
from edpy.perpetual import ensure_perpetual, Perpetual


def expect_stop_async_iteration():
    return pytest.raises(StopAsyncIteration)


def expect_invalid_state():
    return pytest.raises(asyncio.InvalidStateError)


def expect_cancelled():
    return pytest.raises(asyncio.CancelledError)


@pytest.fixture
def perpetual() -> Perpetual:
    return Perpetual()


def test_initial_state(perpetual: Perpetual):
    assert not perpetual.cancelled()
    assert not perpetual.done()
    assert perpetual.empty()

    with pytest.raises(asyncio.InvalidStateError):
        perpetual.result()

    perpetual.cancel()
    assert perpetual.cancelled()


async def test_cancel(perpetual: Perpetual):
    assert perpetual.cancel()
    assert not perpetual.cancel()

    assert perpetual.cancelled()
    assert perpetual.empty()

    with expect_cancelled():
        perpetual.set_result(42)

    with expect_cancelled():
        perpetual.set_exception(ValueError())

    with expect_cancelled():
        perpetual.result()

    with expect_cancelled():
        await perpetual

    with expect_stop_async_iteration():
        await anext(perpetual)

    assert len([res async for res in perpetual]) == 0


async def test_result(perpetual: Perpetual):
    future_result = asyncio.ensure_future(perpetual)
    await asyncio.sleep(0)

    perpetual.set_result(42)

    assert not perpetual.cancelled()
    assert not perpetual.empty()

    assert perpetual.result() == 42
    assert perpetual.exception() is None
    assert (await asyncio.wait_for(future_result, .1)) == 42

    future_result = asyncio.ensure_future(anext(perpetual))

    # TODO removing this causes cancellation of the future
    #  -> use loop.call_soon inside perpetual.set_result
    await asyncio.sleep(0)

    perpetual.set_result('spam')

    assert not perpetual.cancelled()
    assert not perpetual.empty()
    assert perpetual.result() == 'spam'
    assert (await asyncio.wait_for(future_result, .1)) == 'spam'


async def test_exception(perpetual: Perpetual):
    future_result = asyncio.ensure_future(perpetual)

    perpetual.set_exception(ZeroDivisionError('42/0'))

    assert not perpetual.cancelled()
    assert not perpetual.empty()

    assert isinstance(perpetual.exception(), ZeroDivisionError)

    with pytest.raises(ZeroDivisionError):
        perpetual.result()

    with pytest.raises(ZeroDivisionError):
        await perpetual

    with pytest.raises(ZeroDivisionError):
        await future_result


async def test_ensure_perpetual():
    async def _agen():
        await asyncio.sleep(.05)
        yield 'spam'
        await asyncio.sleep(.05)
        yield 'ham'

    perpetual = ensure_perpetual(_agen())

    assert perpetual.empty()
    assert (await asyncio.wait_for(perpetual, .1)) == 'spam'
    assert (await perpetual) == 'spam'

    assert (await asyncio.wait_for(anext(perpetual), .1)) == 'ham'

    with expect_stop_async_iteration():
        await asyncio.wait_for(anext(perpetual), .1)

    assert perpetual.done()
    assert not perpetual.cancelled()

    assert perpetual.result() == 'ham'


async def test_ensure_perpetual_exception():
    async def _agen():
        await asyncio.sleep(.05)
        yield 42 / 1
        await asyncio.sleep(.05)
        yield 42 / 0
        await asyncio.sleep(.05)
        yield 42 / -1

    perpetual = ensure_perpetual(_agen())
    assert perpetual.empty()

    first = await asyncio.wait_for(anext(perpetual), .1)
    assert first == 42

    with pytest.raises(ZeroDivisionError):
        await asyncio.wait_for(anext(perpetual), .1)

    assert perpetual.is_error
    assert not perpetual.cancelled()
    assert not perpetual.done()

    with expect_stop_async_iteration():
        await asyncio.wait_for(anext(perpetual), .1)

    assert perpetual.is_error
    assert perpetual.done()
    assert not perpetual.cancelled()
