"""Test configuration and shared fixtures."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, cast

import pytest
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool
from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

    from _pytest.fixtures import SubRequest
    from sqlalchemy.ext.asyncio import AsyncEngine


@pytest.fixture(
    scope='session',
    params=[
        pytest.param(
            ('asyncio', {'use_uvloop': True}),
            id='asyncio+uvloop',
            marks=[
                pytest.mark.skipif(
                    sys.platform.startswith('win'),
                    reason='uvloop does not support Windows',
                ),
                pytest.mark.skipif(
                    sys.version_info >= (3, 14),
                    reason='uvloop does not yet support Python 3.14+',
                ),
            ],
        ),
        pytest.param(('asyncio', {'use_uvloop': False}), id='asyncio'),
    ],
    autouse=True,  # noqa: RUF076
)
def anyio_backend(request: SubRequest) -> tuple[str, dict[str, object]]:
    return cast('tuple[str, dict[str, object]]', request.param)


@pytest.fixture(scope='session')
def pg_container() -> Iterator[str]:
    with PostgresContainer('postgres:17', driver='psycopg') as pg:
        yield pg.get_connection_url()


@pytest.fixture
async def pg_engine(pg_container: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(pg_container, poolclass=NullPool)
    yield engine
    await engine.dispose()
