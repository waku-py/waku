from dataclasses import dataclass

from waku import WakuFactory

from tests.module_utils import create_basic_module


@dataclass
class _LifespanSpy:
    entered: bool = False
    exited: bool = False

    async def __aenter__(self) -> None:
        self.entered = True

    async def __aexit__(self, *_: object) -> None:
        self.exited = True


async def test_lifespan_managers_entered_and_exited_with_app() -> None:
    manager_1 = _LifespanSpy()
    manager_2 = _LifespanSpy()

    AppModule = create_basic_module(name='AppModule')

    application = WakuFactory(AppModule, lifespan=[manager_1, manager_2]).create()

    async with application:
        assert manager_1.entered
        assert manager_2.entered
        assert not manager_1.exited
        assert not manager_2.exited

    assert manager_1.exited
    assert manager_2.exited  # type: ignore[unreachable]
