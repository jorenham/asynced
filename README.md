# AsyncED

-----

[![PyPI version shields.io](https://img.shields.io/pypi/v/asynced.svg)](https://pypi.python.org/pypi/asynced/)
[![PyPI pyversions](https://img.shields.io/pypi/pyversions/asynced.svg)](https://pypi.python.org/pypi/asynced/)
[![PyPI license](https://img.shields.io/pypi/l/asynced.svg)](https://pypi.python.org/pypi/asynced/)

-----

**Async** python for **E**vent-**D**riven applications

## Installation

```bash
pip install asynced
```

## High-level API

*Coming soon...*

## Low-level API

### Promise

Inspired by (but not a clone of) Javascript promises, `asynced.Promise` is a
thin wrapper around any coroutine. 

Like `asyncio.Task`, when a promise is created, the wrapped coroutine will run 
in the background, and can be awaited to get the result or exception. In 
addition, a `Promise` can be "chained" with sync or async functions, producing 
another `Promise`.

Example:

```pycon
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
    answer = await Promise(formulate_ultimate_question()).then(compute_answer)
    print(answer)


asyncio.run(amain())
```



### Perpetual

Where asyncio futures are the bridge between low-level events and a
coroutines, perpetuals are the bridge between event streams and async
iterators.

In it's essence, a perpetual is an asyncio.Future that can have its result
(or exception) set multiple times, at least until it is stopped. Besides
a perpetual being awaitable just like a future, it is an async iterator as
well.


### ensure_future

Wrap an async iterable in a perpetual, and automatically starts iterating. 

See [perpetual_drumkit.py](examples/perpetual_drumkit.py) for an example.

~

*More docs and examples coming soon...*
