from __future__ import annotations

from dataclasses import dataclass

from typing_extensions import override

from waku.eventsourcing.decider.handler import DeciderVoidCommandHandler
from waku.eventsourcing.modules import EventSourcingConfig, EventSourcingExtension, EventSourcingModule
from waku.eventsourcing.store.in_memory import InMemoryEventStore
from waku.messaging import IRequest, MessagingExtension, MessagingModule
from waku.messaging.interfaces import IMessageBus
from waku.modules import module
from waku.testing import create_test_app

from tests.eventsourcing.decider.conftest import CounterRepository
from tests.eventsourcing.test_decider import CounterDecider, CounterState, Increment, Incremented


@dataclass(frozen=True, kw_only=True)
class IncrementCounter(IRequest):
    counter_id: str
    amount: int


class IncrementCounterHandler(DeciderVoidCommandHandler[IncrementCounter, CounterState, Increment, Incremented]):
    @override
    def _aggregate_id(self, request: IncrementCounter) -> str:
        return request.counter_id

    @override
    def _to_command(self, request: IncrementCounter) -> Increment:
        return Increment(amount=request.amount)


async def test_bind_decider_integrates_with_di_and_message_bus() -> None:
    @module(
        imports=[
            EventSourcingModule.register(EventSourcingConfig(store=InMemoryEventStore)),
            MessagingModule.register(),
        ],
        extensions=[
            EventSourcingExtension().bind_decider(
                repository=CounterRepository,
                decider=CounterDecider,
                event_types=[Incremented],
            ),
            MessagingExtension().bind_request(IncrementCounter, IncrementCounterHandler),
        ],
    )
    class CounterModule:
        pass

    async with create_test_app(imports=[CounterModule]) as app, app.container() as container:
        bus = await container.get(IMessageBus)
        await bus.invoke(IncrementCounter(counter_id='c-1', amount=5))

        repo = await container.get(CounterRepository)
        state, version = await repo.load('c-1')
        assert state == CounterState(value=5)
        assert version == 0
