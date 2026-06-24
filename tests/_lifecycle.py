import anyio
from typing_extensions import override

from waku.extensions import AfterApplicationInit, OnApplicationShutdown

__all__ = ['LifecycleRecorder']


class LifecycleRecorder(AfterApplicationInit, OnApplicationShutdown):
    def __init__(self) -> None:
        self.events: list[str] = []
        self.initialized = anyio.Event()

    @override
    async def after_app_init(self, app: object) -> None:
        self.events.append('init')
        self.initialized.set()

    @override
    async def on_app_shutdown(self, app: object) -> None:
        self.events.append('shutdown')
