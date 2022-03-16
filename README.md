# AsyncED

Async python for Event-Driven applications

## High-level API

*Coming soon...*

## Low-level API

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
