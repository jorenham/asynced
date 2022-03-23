import asyncio
from asynced import Promise


async def formulate_ultimate_question() -> str:
    await asyncio.sleep(0.25)
    return (
        'What is The Answer to the Ultimate Question of Life, the Universe, '
        'and Everything?'
    )


async def compute_answer(question: str):
    await asyncio.sleep(0.75)
    return (len(question) >> 1) + 1


async def amain():
    answer = await Promise(formulate_ultimate_question()).amap(compute_answer)
    print(answer)


asyncio.run(amain())
