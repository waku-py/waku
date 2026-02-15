from __future__ import annotations

from dataclasses import dataclass

from typing_extensions import override

from waku.cqrs import MediatorExtension, MediatorModule, Request
from waku.cqrs.interfaces import IMediator
from waku.eventsourcing.decider.handler import DeciderVoidCommandHandler
from waku.eventsourcing.modules import EventSourcingExtension, EventSourcingModule
from waku.modules import module
from waku.testing import create_test_app

from tests.eventsourcing.decider.conftest import CounterRepository
from tests.eventsourcing.test_decider import CounterDecider, CounterState, Increment, Incremented


@dataclass(frozen=True, kw_only=True)
class IncrementCounter(Request[None]):
    counter_id: str
    amount: int


class IncrementCounterHandler(DeciderVoidCommandHandler[IncrementCounter, CounterState, Increment, Incremented]):
    @override
    def _aggregate_id(self, request: IncrementCounter) -> str:
        return request.counter_id

    @override
    def _to_command(self, request: IncrementCounter) -> Increment:
        return Increment(amount=request.amount)

    @override
    def _is_creation_command(self, request: IncrementCounter) -> bool:
        return True


async def test_bind_decider_integrates_with_di_and_mediator() -> None:
    @module(
        imports=[EventSourcingModule.register(), MediatorModule.register()],
        extensions=[
            EventSourcingExtension().bind_decider(
                repository=CounterRepository,
                decider=CounterDecider,
                event_types=[Incremented],
            ),
            MediatorExtension().bind_request(IncrementCounter, IncrementCounterHandler),
        ],
    )
    class CounterModule:
        pass

    async with create_test_app(imports=[CounterModule]) as app, app.container() as container:
        mediator = await container.get(IMediator)
        await mediator.send(IncrementCounter(counter_id='c-1', amount=5))

        repo = await container.get(CounterRepository)
        state, version = await repo.load('c-1')
        assert state == CounterState(value=5)
        assert version == 0
