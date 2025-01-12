import pytest

pytest_plugins = [
    'anyio',
]


@pytest.fixture(scope='session', autouse=True)
def anyio_backend() -> str:
    return 'asyncio'
