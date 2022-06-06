__all__ = 'input_state',

from typing import AsyncIterator
import sys

import anyio
from asynced.states import StateVar


def input_state() -> StateVar[str]:
    f_stdin = anyio.wrap_file(sys.stdin)

    async def producer() -> AsyncIterator[str]:
        while value := await f_stdin.readline():
            yield value.rstrip()

    return StateVar(producer(), name='<stdin>')


if __name__ == '__main__':
    @anyio.run
    async def __example():
        async with input_state() as stdin:
            print('press "q" to exit ...')

            async for c in stdin:
                print(f'>>> {c!r}')
                if c.lower() == 'q':
                    break

            print('exiting...')

        print('done...')
