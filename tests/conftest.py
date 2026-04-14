import pytest

import server


@pytest.fixture(autouse=True)
def clean_store():
    """Reset the in-memory store between tests."""
    server.store._data = {} if hasattr(server.store, "_data") else server.store._data
    if hasattr(server.store, "_data"):
        server.store._data.clear()
    yield
    if hasattr(server.store, "_data"):
        server.store._data.clear()
