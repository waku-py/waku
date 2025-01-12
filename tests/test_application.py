from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import pytest

from tests.mock import DummyDI
from waku import Application, Module
from waku.application import ApplicationConfig
from waku.di import Scoped, Singleton

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


class MockStartupExtension:
    def __init__(self) -> None:
        self.startup_called = False

    async def on_app_startup(self, app: Application) -> None:  # noqa: ARG002
        self.startup_called = True


class MockShutdownExtension:
    def __init__(self) -> None:
        self.shutdown_called = False

    async def on_app_shutdown(self, app: Application) -> None:  # noqa: ARG002
        self.shutdown_called = True


class MockLifespanManager:
    def __init__(self) -> None:
        self.entered = False
        self.exited = False

    @asynccontextmanager
    async def __call__(self, app: Application) -> AsyncIterator[None]:  # noqa: ARG002
        self.entered = True
        yield
        self.exited = True


@pytest.fixture
def di_provider() -> DummyDI:
    return DummyDI()


async def test_application_lifecycle(di_provider: DummyDI) -> None:
    """Test application startup and shutdown lifecycle."""
    startup_ext = MockStartupExtension()
    shutdown_ext = MockShutdownExtension()
    lifespan = MockLifespanManager()

    app = Application(
        name='test_app',
        config=ApplicationConfig(
            modules=[],
            dependency_provider=di_provider,
            extensions=[startup_ext, shutdown_ext],
            lifespan=[lifespan],
        ),
    )

    async with app:
        assert startup_ext.startup_called
        assert not shutdown_ext.shutdown_called
        assert lifespan.entered
        assert not lifespan.exited

    assert shutdown_ext.shutdown_called
    assert lifespan.exited  # type: ignore[unreachable]


async def test_application_module_registration(di_provider: DummyDI) -> None:
    """Test that modules are properly registered with the application."""

    class ServiceA:
        pass

    class ServiceB:
        pass

    module_a = Module(name='module_a', providers=[Scoped(ServiceA)], exports=[ServiceA])
    module_b = Module(name='module_b', providers=[Singleton(ServiceB)], imports=[module_a])
    app = Application(
        name='test_app',
        config=ApplicationConfig(
            modules=[module_a, module_b],
            dependency_provider=di_provider,
        ),
    )

    async with app:
        assert di_provider.is_registered(ServiceA)
        assert di_provider.is_registered(ServiceB)


async def test_multiple_lifespan_managers(di_provider: DummyDI) -> None:
    """Test that multiple lifespan managers work correctly."""
    manager1 = MockLifespanManager()
    manager2 = MockLifespanManager()

    app = Application(
        name='test_app',
        config=ApplicationConfig(
            modules=[],
            dependency_provider=di_provider,
            lifespan=[manager1, manager2],
        ),
    )

    async with app:
        assert manager1.entered
        assert manager2.entered
        assert not manager1.exited
        assert not manager2.exited

    assert manager1.exited
    assert manager2.exited  # type: ignore[unreachable]
