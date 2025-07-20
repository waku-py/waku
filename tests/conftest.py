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
            marks=pytest.mark.skipif(
                sys.platform.startswith('win'),
                reason='uvloop does not support Windows',
            ),
        ),
        pytest.param(('asyncio', {'use_uvloop': False}), id='asyncio'),
        pytest.param(('trio', {'restrict_keyboard_interrupt_to_checkpoints': True}), id='trio'),
    ],
    autouse=True,
)
def anyio_backend(request: SubRequest) -> tuple[str, dict[str, object]]:
    return cast(tuple[str, dict[str, object]], request.param)  # ty: ignore[possibly-unbound-attribute]
