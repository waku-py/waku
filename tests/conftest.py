"""Test configuration and shared fixtures."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, cast

import pytest

if TYPE_CHECKING:
    from _pytest.fixtures import SubRequest


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
    autouse=True,
)
def anyio_backend(request: SubRequest) -> tuple[str, dict[str, object]]:
    return cast('tuple[str, dict[str, object]]', request.param)
