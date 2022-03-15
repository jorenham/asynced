__all__ = (
    'ensure_event_loop',
)

import asyncio


def ensure_event_loop() -> asyncio.AbstractEventLoop:
    """Return the running event loop if exists, otherwise creates a new one.
    """
    return asyncio.get_event_loop_policy().get_event_loop()
