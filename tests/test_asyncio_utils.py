import asyncio

from asynced import race


async def test_race():
    async def slowrange(t0, dt, *args):
        await asyncio.sleep(t0)
        for i in range(*args):
            yield i
            await asyncio.sleep(dt)

    dt = 0.05
    n = 3

    it_0_2 = slowrange(dt * 0, dt * 2, n)
    it_1_2 = slowrange(dt * 1, dt * 2, n)

    res = [(i, j) async for i, j in race(it_0_2, it_1_2)]
    assert len(res) == 2 * n

    res_i = [i for i, _ in res]

    assert not any(res_i[::2])
    assert all(res_i[1::2])

    res_j = [j for _, j in res]
    assert res_j[::2] == res_j[1::2]
