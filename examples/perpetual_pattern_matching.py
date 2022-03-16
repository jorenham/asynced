import asyncio
import sys

from asynced import Perpetual


def match_perpetual(p: Perpetual):
    match p:
        case Perpetual(None):
            print('empty')
        case Perpetual(Exception()):
            print(f'error: {p.exception()!r}')
        case Perpetual(result):
            print(f'result: {result!r}')
        case Perpetual():
            raise TypeError


async def amain():
    p = Perpetual()
    match_perpetual(p)

    p.set_result('spam')
    match_perpetual(p)

    p.set_exception(ZeroDivisionError())
    match_perpetual(p)


if __name__ == '__main__':
    if sys.version_info < (3, 10):
        raise SystemError('requires Python 3.10')

    asyncio.run(amain())
