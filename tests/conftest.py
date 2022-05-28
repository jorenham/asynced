import pytest

pytest.mark.usefixtures('anyio_backend')


@pytest.fixture
def anyio_backend():
    return 'asyncio'
