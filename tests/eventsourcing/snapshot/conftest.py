from __future__ import annotations

import pytest


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if 'sqlalchemy' in item.nodeid and '[trio]' in item.nodeid:
            item.add_marker(pytest.mark.skip(reason='aiosqlite is asyncio-only'))
