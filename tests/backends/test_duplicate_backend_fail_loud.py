from __future__ import annotations

import pytest
from dishka.exceptions import ImplicitOverrideDetectedError
from sqlalchemy.ext.asyncio import AsyncSession

from waku.backends.memory import MemoryBackend
from waku.backends.sqlalchemy import SqlAlchemyBackend
from waku.messaging import MessagingConfig, MessagingModule
from waku.testing import create_test_app


def _session_factory() -> AsyncSession:  # pragma: no cover - build fails before any session exists
    return AsyncSession()


async def test_two_backends_in_one_app_fail_the_container_build() -> None:
    # One app has exactly one backend: two providers for one store port fail loudly at container
    # build (dishka names both conflicting providers) — custom = replace, not overlay.
    with pytest.raises(ImplicitOverrideDetectedError):
        async with create_test_app(
            imports=[
                MessagingModule.register(MessagingConfig()),
                SqlAlchemyBackend.register(session_factory=_session_factory),
                MemoryBackend.register(),
            ],
        ):
            pass  # pragma: no cover
