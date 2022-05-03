import asyncio
from typing import Final

import pytest

_TIMEOUT_PREFIX: Final[str] = 'timeout'


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_setup(item: pytest.Item):
    """Wrap all tests marked with pytest.mark.asyncio with their specified timeout.

    Must run as early as possible.

    From: https://stackoverflow.com/a/70095871/1627479
    """
    yield
    orig_obj = item.obj
    timeouts = [n for n in item.funcargs if n.startswith(_TIMEOUT_PREFIX)]
    # Picks the closest timeout fixture if there are multiple
    tname = None if len(timeouts) == 0 else timeouts[-1]

    # Only pick marked functions
    if item.get_closest_marker('asyncio') is not None and tname is not None:

        async def new_obj(*args, **kwargs):
            """Timed wrapper around the test function."""
            try:
                return await asyncio.wait_for(
                    orig_obj(*args, **kwargs), timeout=item.funcargs[tname]
                )
            except Exception as e:
                pytest.fail(f'Test {item.name} did not finish in time.')

        item.obj = new_obj
