import asyncio
from asynced import Promise


async def amain():
    await (
        Promise(asyncio.sleep(.5, 'spam'))
        .then(str.title)
        .then(lambda x: x * 3)
        .then(print)
    )


if __name__ == '__main__':
    asyncio.run(amain())
