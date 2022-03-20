import asyncio
from typing import Final, NoReturn, TypeVar

from asynced import Promise

_T = TypeVar('_T')


DELAY: Final[float] = 1.0


async def print_and_pass(arg: _T) -> _T:
    print(repr(arg))
    return await asyncio.sleep(DELAY, arg)


async def print_and_raise(arg: Exception) -> NoReturn:
    print(repr(arg))
    await asyncio.sleep(DELAY)
    raise arg


async def amain() -> None:
    res = await (
        Promise
        .resolve('spam')
        .then(print_and_pass).except_(print_and_raise)
        .then(len)
        .then(print_and_pass).except_(print_and_raise)
        .then(lambda n: n / 0)
        .then(print_and_pass).except_(print_and_raise)
        .then(lambda n: (n - 1) * 666)  # skipped
        .then(print_and_pass).except_(print_and_raise)
        .except_(lambda e: 42)
        .then(print_and_pass).except_(print_and_raise)
    )
    print()
    print(f'result: {res!r}')


if __name__ == '__main__':
    asyncio.run(amain())
