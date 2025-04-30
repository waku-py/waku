"""Tests for application lifecycle management."""

from dataclasses import dataclass

from tests.module_utils import create_basic_module
from waku import WakuFactory


@dataclass
class MockLifespanManager:
    """A mock lifespan manager for testing."""

    entered: bool = False
    exited: bool = False

    async def __aenter__(self) -> None:
        self.entered = True

    async def __aexit__(self, *_: object) -> None:
        self.exited = True


async def test_application_lifespan_manager_execution() -> None:
    """Application should execute lifespan managers in order and handle their lifecycle correctly."""
    manager_1 = MockLifespanManager()
    manager_2 = MockLifespanManager()

    AppModule = create_basic_module(name='AppModule')

    application = WakuFactory(AppModule, lifespan=[manager_1, manager_2]).create()

    async with application:
        assert manager_1.entered
        assert manager_2.entered
        assert not manager_1.exited
        assert not manager_2.exited

    assert manager_1.exited
    assert manager_2.exited  # type: ignore[unreachable]
