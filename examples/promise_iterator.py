import asyncio
import itertools

from asynced import PromiseIterator


async def exp_backoff(start: int = 0, base: float = 2, timeout: float = 60):
    t = base ** start
    while t < timeout:
        await asyncio.sleep(t / 10)
        yield t
        t *= base

    # raise asyncio.TimeoutError


async def amain():
    p = PromiseIterator(exp_backoff())
    await asyncio.sleep(1)
    async for t in p:
        print(t)


asyncio.run(amain())
