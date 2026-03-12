from __future__ import annotations

from dataclasses import dataclass

from typing_extensions import override

from waku.messaging.contracts.event import IEvent
from waku.messaging.events.handler import EventHandler
from waku.messaging.events.publish import GroupEventPublisher, SequentialEventPublisher


@dataclass(frozen=True)
class _TestEvent(IEvent):
    value: str = 'test'


class _TrackingHandler(EventHandler[_TestEvent]):
    def __init__(self) -> None:
        self.calls: list[str] = []

    @override
    async def handle(self, event: _TestEvent, /) -> None:
        self.calls.append(event.value)


async def test_sequential_publisher_calls_handlers_in_order() -> None:
    publisher = SequentialEventPublisher()
    h1 = _TrackingHandler()
    h2 = _TrackingHandler()

    await publisher([h1, h2], _TestEvent(value='hello'))

    assert h1.calls == ['hello']
    assert h2.calls == ['hello']


async def test_group_publisher_calls_all_handlers() -> None:
    publisher = GroupEventPublisher()
    h1 = _TrackingHandler()
    h2 = _TrackingHandler()

    await publisher([h1, h2], _TestEvent(value='hello'))

    assert h1.calls == ['hello']
    assert h2.calls == ['hello']
