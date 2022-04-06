
import time
from typing import AsyncGenerator, Final

from asynced import StateVar, statezip
from asynced._compat import amap
import asyncio

from math import cos, sqrt, log as ln, tau as TAU
import random

from mainpy import main


DT: Final[float] = 0.01


def uniform() -> float:
    return random.uniform(0, 1)


async def uniform_process(dt: float) -> AsyncGenerator[float, None]:
    assert dt > 0

    while True:
        yield await asyncio.sleep(dt, uniform())


def box_muller(a: float, b: float) -> float:
    assert 0 <= a <= 1 and 0 <= b <= 1
    return sqrt(-2 * ln(a)) * cos(TAU * b)


# noinspection PyPep8Naming
@main
async def statevars_example():
    # time-related utility functions
    t0 = time.monotonic()

    def t() -> float:
        return time.monotonic() - t0

    def tprint(*args):
        _t = round(t() / DT) * DT  # quantize w.r.t dt
        print(f'[t={_t:.4g}]', *args)

    # Two independent and i.i.d. uniformly distributed random variables
    U_a: StateVar[float] = StateVar(uniform_process(DT))
    U_b: StateVar[float] = StateVar(uniform_process(DT))

    print('await ...')

    # Lets print their first values:
    tprint(await U_a, await U_b)
    assert t() > DT

    # awaiting again (within the 'dt' timespan) shows that they haven't changed
    tprint(await U_a, await U_b)

    # after > 'dt' seconds, they change:
    await asyncio.sleep(DT * 1.5)
    tprint(await U_a, await U_b)

    print('\n.next()')

    # to get the next value more easily, we can use the .next() method instead,
    #  to that we don't wait any longer than we really have to
    tprint(await U_a.next(), await U_b.next())

    print('\nstatezip(...)')

    # We can combine the two using the asynced version of zip, that acts on
    # statevars, and returns  statevar. We use sync=True to indicate that
    # we expect both to change simultaneously, avoiding that one goes faster
    # than the other
    U_ab = statezip(U_a, U_b, sync=True)
    tprint(await U_ab)

    # and we can use .next() on it just like the individual statevars:
    tprint(await U_ab.next())

    # or use it in a loop:
    i = 0
    async for u_a, u_b in U_ab:
        tprint(u_a, u_b)

        i += 1
        if i >= 4:
            break

    print('\nstatemap(...)')

    # like zip, we can combine the two using a function with statemap:
    U_c = amap(lambda u_a, u_b: (u_a + u_b) / 2, U_a, U_b)

    # let's look at the args and result together:
    print(await statezip(U_ab, U_c, sync=True))

    # TODO
    #  rename: filter(self, predicate)) -> where(self, predicate)
    #  add: split(), like starmap, but returns multiple statevalues, inv. of zip
    #  add: join(), like zip, but with "gather" instead of "race"
