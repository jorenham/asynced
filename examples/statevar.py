
import time
from typing import AsyncGenerator, Final

from asynced import StateVar
import asyncio
from aioitertools.itertools import islice

import random

from mainpy import main


DT: Final[float] = 0.01
N: Final[int] = 100


async def uniform_process(dt: float, n: int) -> AsyncGenerator[float, None]:
    assert dt > 0

    for _ in range(n):
        yield await asyncio.sleep(dt, random.uniform(0, 1))


# noinspection PyPep8Naming
@main
async def statevars_example():
    # time-related utility functions
    def t() -> float:
        return time.monotonic() - t0

    def cmdprint(arg='', *args):
        print(f'\n>>> {arg}', *args, sep='\n... ')

    def tprint(*args):
        _t = int(t() / DT) * DT  # quantize w.r.t dt
        print(
            *(repr(arg)[:10] for arg in args),
            '\t' * (8 - len(args)),
            f'[t={_t:.4g}]', sep='\t'
        )

    # i.i.d. uniformly distributed random variable
    U: StateVar[float] = StateVar(uniform_process(DT, N))
    t0 = time.monotonic()

    # wait until a value is set:
    cmdprint('await U')
    tprint(await U)
    assert t() > DT

    # when we wait again within the 'dt' timespan, nothing has changed
    cmdprint('await U')
    tprint(await U)

    # After > 'dt' seconds, we see that it changed:
    cmdprint('asyncio.sleep(DT)', 'await U')
    await asyncio.sleep(DT * 1.1)
    tprint(await U)

    # To get the next value more easily, we can use the .next() method instead,
    #  to that we don't wait any longer than we really have to.
    cmdprint('await U.next()')

    tprint(await U.next())

    # Conveniently, statevars are also async iterable, and start from the
    # current value
    cmdprint('aiter(U)')

    async for u in islice(U, 5):
        tprint(u)

    # Let's create a new bernoulli random variable from the uniform one:
    cmdprint('B = U.map(round)')
    B = U.map(round)

    async for b in islice(B, 5):
        tprint(await U, b)

    # U will stop changing after N * DT iterations (although statevars are
    # allowed to go on forever). Let's see if we're there yet:
    cmdprint('U.is_done')
    tprint('done' if U.is_done else 'not done')

    # It's still busy, but we can wait until it stops:
    cmdprint('await U.is_exhausted')
    tprint(await U.is_exhausted)

    # The state is now frozen, and its value is still accessible:
    cmdprint('await U')
    tprint(await U)

    # We can also still iterate over it:
    cmdprint('aiter(U)')
    async for u in islice(U, 5):
        tprint(u)
