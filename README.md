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

## Usage

At the core of `asynced` lies `StateVar`. It can (but does not have to) contain 
a value, can be watched for changed, and can easily be mapped to other 
`StateVar` instances.


*For the sake of brevity, next examples are assumed to be run from within an async function, as `StateVar` requires a running event loop.*.

Creating an empty state var is as easy as:

```pycon
>>> from asynced import StateVar
>>> spam = StateVar()
```

We can check whether it is set:

```pycon
>>> spam.is_set
False
```

Let's create an `asyncio.Task` that prints the spam value once it's set.

```pycon
>>> async def print_spam():
...     print(await spam)
... 
... task = asyncio.create_task(print_spam)
```

Nothing is printed yet until we set the statevar:

```pycon
>>> spam.set('Spam!')
Spam!
```

After we set the statevar, we see that `print_spam` printed the value.

Up to here, we've done nothing that `asyncio.Future` cannot do. But an 
`asyncio.Future` can only be set once, whereas a `StateVar` can be set to a 
different value as much as needed. 

We can watch for any changes of a statevar by async iteration:

```pycon
>>> async def print_spam_changes():
...     async for value in spam:
...         print(value)
... 
... task = asyncio.create_task(print_spam_changes)
```

Now any time we set `spam` to a different value, it'll be printed:

```pycon
>>> spam.set('Spam and ham!')
Spam and ham!
>>> spam.set('Spam and eggs!')
Spam and eggs!
```

Neat eh?

A `StateVar` can also be constructed by passing async iterable, making it 
behave more like an `asyncio.Task`:

```pycon
>>> async def slowrange(*args):
...     for i in range(*args):
...         await asyncio.sleep(1)
...         yield i
...
>>> count4 = StateVar(slowrange(4))
>>> await asyncio.sleep(3.14)
>>> await count4
2
```

As we see here, the statevar will set itself to the values from the async 
iterable in the background automatically. 

### Mapping

`StateVar`'s can also be constructed by applying a function to another 
`StateVar`:

```pycon
>>> count4_inv = StateVar(slowrange(4)).map(lambda i: -i)
>>> async for i in count4_inv:
...     print(i)
```

Now with one-second intervals, `0`, `-1`, `-2` and `-3` will be printed.

`StateVar.map` only works for functions with a single argument, i.e. for one 
statevar at a time. But fear not, mapping multiple statevars together is 
possible by using `asynced.statefunction`. It transforms any function 
`(a: A, b: B, ...) -> R` into one that accepts `State`'s (`State` is the superclass of `StateVar`) and returns a 
new `StateVar` (or some other that subclasses `State`), 
`(a: State[A], b: State[B], ...) -> StateVar[R]`. 

```pycon
>>> from asynced import statefunction
>>> @statefunction
... def sadd(_a: float, _b: float) -> float:
...    return _a + _b
... 
>>> a, b = StateVar(), StateVar()
>>> c = sadd(a, b)
```

Here, `c` will be set iff both `a` and `b` are set:

```pycon
>>>import calendar c.is_set
False
>>> a.set(12)
c.is_set
False
>>> b.set(30)
>>> await c
42
```

Now, every time that `a` or `b` change, `c` will change as well.

### `StateTuple`

In the same way as `StateVar`, a `StateTuple` can be awaited to get the current
value once it's available, and async-iterated to get the values once they are 
set. Additionally, it can also be used as a tuple of individual `StateVar` 
instances. 

```pycon
>>> st = StateTuple(2)  # equivalent StateTuple(StateVar(), StateVar())
>>> st[0].set('spam')
>>> st[1].set('ham')
>>> await st
('spam', 'ham')
>>> await st[-1]
'ham'
>>> st[1] = 'eggs'  # equivalent to st[1].set('eggs')
>>> await st
('spam', 'eggs')
>>> s0, s1 = st
>>> await s1
'eggs'
>>> st2 = 2 * st  # equivalent to StateTuple((st[0], st[1], st[0], st[1]))
>>> st2[2] = 'bacon'
>>> await st2
('bacon', 'eggs', 'bacon', 'eggs')
>>> await st
('bacon', 'eggs')
```

### `StateDict`

Like `StateTuple`, a `StateDict` is a collection of individual `StateVar`'s. 
It is more similar to a `collections.defaultdict` than a regular dict, because
accessing keys without values will return an empty `StateVar`.

```pycon
>>> sd = StateDict()
>>> await sd
{}
>>> sd['spam'] = 'Spam!'
>>> await sd
{'spam': 'Spam!'}
>>> ham = sd['ham']
>>> ham.is_set
False
>>> list(sd.keys())
['spam']
>>> sd['ham'] = 'Ham and eggs!'
>>> await ham
'Ham and eggs!'
```

### `.get()`

`StateVar`, `StateTuple` and `StateDict` all implement a `.get()` method,
that can be used to get the current value, without having to use `await`.
If no value has been set, it will raise a `LookupError`. Alternatively,
you can pass a `default` value, that is to be returned in case no value is set.

### Error handling

If the async iterable used to create e.g. a `StateVar` raises an error,
the `.is_error` property will become `True`, and the error will be raised when
its awaited, or when`.get` or `.map` are called. The exception will propagate
to its mapped "child" `State`'s.

If the async iterable is exhausted (or raises `StopAsyncIteration` directly),
`.is_stopped` will be set to `True` instead, and `StopAsyncIteration` will be **only** reraised for the waiters of `__aiter__` and `__anext__`.

## API reference

*~ coming soon ~*

