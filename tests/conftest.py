"""Test configuration and shared fixtures."""

import sys
from typing import cast

import pytest


@pytest.fixture(
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
def anyio_backend(request: pytest.FixtureRequest) -> tuple[str, dict[str, object]]:
    return cast(tuple[str, dict[str, object]], request.param)
