"""Test configuration and shared fixtures."""

from typing import Any, cast

import pytest


@pytest.fixture(
    params=[
        pytest.param(('asyncio', {'use_uvloop': True}), id='asyncio+uvloop'),
        pytest.param(('asyncio', {'use_uvloop': False}), id='asyncio'),
        pytest.param(('trio', {'restrict_keyboard_interrupt_to_checkpoints': True}), id='trio'),
    ],
    autouse=True,
)
def anyio_backend(request: pytest.FixtureRequest) -> tuple[str, dict[str, Any]]:
    return cast(tuple[str, dict[str, Any]], request.param)
