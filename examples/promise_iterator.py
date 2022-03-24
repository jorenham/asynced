import asyncio
import itertools

from asynced import PromiseIterator


async def arange(*args):
    for i in range(*args):
        yield await asyncio.sleep(0, i)


def exprange

async def amain():
    exprange = PromiseIterator(arange()).map(lambda i: i**2)

    async for t in p:
        print(t)


asyncio.run(amain())
