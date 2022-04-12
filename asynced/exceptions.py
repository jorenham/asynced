from __future__ import annotations

__all__ = (
    'StopAnyIteration',

    'StateError',
)


import asyncio
from typing import Final


StopAnyIteration: Final = StopIteration, StopAsyncIteration, GeneratorExit


class StateError(asyncio.InvalidStateError):
    """Base class for state-related errors"""
    pass
