import asyncio
from typing import Final

from asynced import StateVar, StateDict


DT: Final[float] = 0.01


async def test_initial():
    s: StateDict[str, str] = StateDict()

    assert not s

    assert await s == {}
    assert s.get() == {}

    assert len(s) == 0
    assert len(list(s)) == 0
    assert len(list(s.keys())) == 0
    assert len(list(s.values())) == 0
    assert len(list(s.items())) == 0

    assert not s.is_set
    assert not s.is_stopped
    assert not s.is_error
    assert not s.is_cancelled
    assert not s.is_done

    assert not s.any_set
    assert not s.any_stopped
    assert not s.any_error
    assert not s.any_cancelled
    assert not s.any_done

    assert s.all_set
    assert s.all_stopped
    assert s.all_error
    assert s.all_cancelled
    assert s.all_done

    assert 'spam' not in s
    s_spam = s['spam']
    assert 'spam' not in s
    assert isinstance(s_spam, StateVar)

    assert s['spam'] is s_spam
    assert s.get('spam', None) is None

    assert not s_spam.is_set
    assert not s_spam.is_done


async def test_initial_kwarg_set():
    sv: StateVar[int] = StateVar()
    s: StateDict[str, int] = StateDict(spam=sv)

    assert await s == {}
    assert s.get() == {}
    assert len(s) == 0
    assert len(list(s)) == 0

    assert 'spam' not in s
    assert s['spam'] is sv
    assert list(s.keys()) == []
    assert list(s.values()) == []
    assert list(s.items()) == []

    sv.set(42)

    assert await s == {'spam': 42}
    assert s.get() == {'spam': 42}
    assert len(s) == 1

    assert list(s) == ['spam']
    assert 'spam' in s
    assert s['spam'] is sv

    assert len(await s) == 1
    assert (await s)['spam'] == 42
    assert await s['spam'] == 42
    assert s.get('spam') == 42
    assert list(s.keys()) == ['spam']
    assert list(s.values()) == [42]
    assert list(s.items()) == [('spam', 42)]


async def test_set_item():
    s = StateDict()
    s['ham'] = 6

    assert 'ham' in s
    assert s.get('ham') == 6
    assert await s['ham'] == 6


async def test_producer():
    async def producer():
        await asyncio.sleep(DT)
        yield 'spam', 42
        await asyncio.sleep(DT)
        yield 'ham', 6
        await asyncio.sleep(DT)
        yield 'spam', 69

    s = StateDict(producer())
    ss = [d async for d in s]

    assert s.is_done
    assert len(ss) == 4

    assert ss[0] == {}
    assert ss[1] == {'spam': 42}
    assert ss[2] == {'spam': 42, 'ham': 6}
    assert ss[3] == {'spam': 69, 'ham': 6}

    assert await s == {'spam': 69, 'ham': 6}


async def test_head():
    s = StateDict()
    assert not s.head.is_set

    s[0] = 'a'
    assert await s.head == (0, 'a')

    s[1] = 'b'
    assert await s.head == (1, 'b')

    s[0] = 'c'
    assert await s.head == (0, 'c')

    del s[0]
    assert await s.head == (0, None)
