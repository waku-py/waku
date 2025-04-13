from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import pytest

from tests.mock import DummyDI
from waku import WakuApplication, WakuFactory
from waku.di import Scoped, Singleton
from waku.modules import module

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


class MockLifespanManager:
    def __init__(self) -> None:
        self.entered = False
        self.exited = False

    @asynccontextmanager
    async def __call__(self, _: WakuApplication) -> AsyncIterator[None]:
        self.entered = True
        yield
        self.exited = True


@pytest.fixture
def di_provider() -> DummyDI:
    return DummyDI()


async def test_application_lifespan(di_provider: DummyDI) -> None:
    """Test application startup and shutdown lifecycle."""
    manager_1 = MockLifespanManager()
    manager_2 = MockLifespanManager()

    @module()
    class AppModule:
        pass

    application = WakuFactory.create(
        AppModule,
        dependency_provider=di_provider,
        lifespan=[manager_1, manager_2],
    )

    async with application:
        assert manager_1.entered
        assert manager_2.entered
        assert not manager_1.exited
        assert not manager_2.exited

    assert manager_1.exited
    assert manager_2.exited  # type: ignore[unreachable]


async def test_application_module_registration(di_provider: DummyDI) -> None:
    """Test that modules are properly registered with the application."""

    class ServiceA:
        pass

    class ServiceB:
        pass

    @module(providers=[Scoped(ServiceA)], exports=[ServiceA])
    class ModuleA:
        pass

    @module(providers=[Singleton(ServiceB)], imports=[ModuleA])
    class ModuleB:
        pass

    @module(imports=[ModuleA, ModuleB])
    class AppModule:
        pass

    application = WakuFactory.create(
        AppModule,
        dependency_provider=di_provider,
    )

    async with application:
        assert di_provider.is_registered(ServiceA)
        assert di_provider.is_registered(ServiceB)
