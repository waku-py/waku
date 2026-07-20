from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from typing_extensions import override

from waku.messages import IEvent
from waku.messaging import (
    EventHandler,
    IMessageBus,
    MessagingConfig,
    MessagingExtension,
    MessagingModule,
)
from waku.messaging.router import local_queue, route
from waku.testing import create_test_app


@dataclass(frozen=True)
class _TaskCreated(IEvent):
    task_id: str


class _TaskHandler(EventHandler[_TaskCreated]):
    received: ClassVar[list[str]] = []

    @override
    async def handle(self, event: _TaskCreated, /) -> None:
        self.received.append(event.task_id)


class TestEndpointLifecycle:
    @staticmethod
    async def test_endpoint_starts_on_app_init_and_processes_messages() -> None:
        _TaskHandler.received.clear()

        config = MessagingConfig(
            endpoints=[local_queue('task-queue')],
            routing=[route(_TaskCreated).to('task-queue')],
        )

        async with (
            create_test_app(
                imports=[MessagingModule.register(config)],
                extensions=[MessagingExtension().bind(_TaskHandler)],
            ) as app,
            app.container() as container,
        ):
            bus = await container.get(IMessageBus)
            await bus.publish(_TaskCreated(task_id='T-1'))

        assert _TaskHandler.received == ['T-1']
