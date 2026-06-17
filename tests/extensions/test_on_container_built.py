from __future__ import annotations

from typing import TYPE_CHECKING

from typing_extensions import override

from waku import WakuFactory
from waku.extensions import AfterApplicationInit, OnContainerBuilt

from tests.module_utils import create_basic_module

if TYPE_CHECKING:
    from waku.application import WakuApplication


class _OrderProbe(OnContainerBuilt, AfterApplicationInit):
    def __init__(self) -> None:
        self.order: list[str] = []
        self.container_present = False

    @override
    async def on_container_built(self, app: WakuApplication) -> None:
        self.order.append('container_built')
        self.container_present = app.container is not None

    @override
    async def after_app_init(self, app: WakuApplication) -> None:
        self.order.append('after_init')


async def test_on_container_built_runs_once_after_container_and_before_after_init() -> None:
    probe = _OrderProbe()
    app_module = create_basic_module(name='AppModule', extensions=[probe])

    app = WakuFactory(app_module).create()
    async with app:
        pass

    assert probe.order == ['container_built', 'after_init']
    assert probe.container_present is True
