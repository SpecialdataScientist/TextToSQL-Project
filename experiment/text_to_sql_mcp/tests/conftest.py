import pytest

from column_mcp.catalog import load_catalog


@pytest.fixture(scope="session")
def catalog():
    return load_catalog()


@pytest.fixture
def anyio_backend():
    return "asyncio"
