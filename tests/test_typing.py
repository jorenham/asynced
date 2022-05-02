import asyncio

import pytest

# noinspection PyProtectedMember
from asynced._typing import acallable, awaitable, ishashclass


async def test_awaitable():
    assert not awaitable(object())

    assert awaitable(asyncio.sleep(0))
    assert awaitable(asyncio.get_running_loop().create_future())

    class Spam:
        def __await__(self):
            return asyncio.sleep(0).__await__()

    assert not awaitable(Spam)
    assert awaitable(Spam())


async def test_acallable():
    def sfunc():
        ...

    async def afunc():
        ...

    class AFunc:
        def __await__(self):
            return asyncio.sleep(0).__await__()

        def __call__(self, *args, **kwargs):
            return self

    assert not acallable(sfunc)
    assert not acallable(lambda: ...)

    loop = asyncio.get_running_loop()
    assert not acallable(loop.create_future)
    assert not acallable(lambda: loop.create_future)

    assert acallable(asyncio.sleep)
    assert acallable(afunc)
    assert not acallable(AFunc)
    assert not acallable(AFunc())


def test_ishashclass():
    assert ishashclass(object)
    assert not ishashclass(set)


def test_ishashclass_object():
    with pytest.raises(TypeError):
        # noinspection PyTypeChecker
        ishashclass(object())


def test_ishashclass_generic():
    assert ishashclass(tuple[str])
    assert not ishashclass(set[str])
    assert not ishashclass(tuple[set[str]])
