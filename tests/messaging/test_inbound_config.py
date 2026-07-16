from typing import Any

import pytest
from typing_extensions import override

from waku._internal.sentinel import MISSING
from waku.di import object_
from waku.exceptions import ImproperlyConfiguredError
from waku.messages import IMessage
from waku.messaging.circuit_breaker.config import CircuitBreakerConfig
from waku.messaging.config import MessagingConfig
from waku.messaging.durability import IDurabilityStore, IInboxStore
from waku.messaging.inbox.backpressure import BufferingLimits
from waku.messaging.inbox.config import InboxConfig
from waku.messaging.modules import MessagingModule
from waku.messaging.router import listen
from waku.messaging.sequence import ISequenceAllocator
from waku.messaging.transport.inbound import ConsumeCallback
from waku.messaging.transport.interfaces import EnvelopeMetadata, IEnvelopeMapper, ITransport, Subscription
from waku.testing import create_test_app
from waku.uow import IUnitOfWork

from tests.messaging.helpers import (
    RecordingAllocator,
    RecordingDeadLetterStore,
    RecordingDurabilityStore,
    RecordingUoW,
    StubSubscription,
)
from tests.messaging.inbox.fake_store import FakeInboxStore
from tests.messaging.outbox.fake_store import RecordingOutboxStore


def _durability(inbox: IInboxStore, unit_of_work: IUnitOfWork) -> RecordingDurabilityStore:
    return RecordingDurabilityStore(
        unit_of_work=unit_of_work,
        outbox=RecordingOutboxStore(),
        inbox=inbox,
        dead_letters=RecordingDeadLetterStore(),
    )


class _StubTransport(ITransport):
    @override
    async def send(
        self,
        body: dict[str, Any],
        *,
        destination: str,
        metadata: EnvelopeMetadata,
        mapper: IEnvelopeMapper[Any, Any] | None = None,
    ) -> None: ...

    @override
    def subscribe(
        self,
        queue: str,
        on_message: ConsumeCallback,
        mapper: IEnvelopeMapper[Any, Any] | None = None,
    ) -> Subscription:
        return StubSubscription()

    @override
    async def start(self) -> None: ...

    @override
    async def stop(self) -> None: ...


def _partition_key(_message: IMessage) -> str:
    return 'k'


def test_listen_builds_entry_with_inherited_requeue() -> None:
    e = listen('orders')
    assert e.uri == 'orders'
    assert e.listen is not None
    assert e.listen.max_requeue_attempts is MISSING  # type: ignore[comparison-overlap]  # mypy lacks PEP 661 sentinel support


def test_listen_builds_entry_with_explicit_requeue() -> None:
    e = listen('orders', max_requeue_attempts=3)
    assert e.uri == 'orders'
    assert e.listen is not None
    assert e.listen.max_requeue_attempts == 3


def test_listen_defaults_carry_no_backpressure_and_inherited_circuit_breaker() -> None:
    e = listen('orders')
    assert e.listen is not None
    assert e.listen.backpressure is None
    assert e.listen.circuit_breaker is MISSING  # type: ignore[comparison-overlap]  # mypy lacks PEP 661 sentinel support


def test_listen_carries_backpressure_and_circuit_breaker() -> None:
    limits = BufferingLimits(high=100, low=20)
    breaker = CircuitBreakerConfig(minimum_throughput=1)
    e = listen('orders', backpressure=limits, circuit_breaker=breaker)
    assert e.listen is not None
    assert e.listen.backpressure is limits
    assert e.listen.circuit_breaker is breaker


async def test_consumer_boots_with_backpressure_and_circuit_breaker() -> None:
    unit_of_work = RecordingUoW()
    inbox = FakeInboxStore()
    config = MessagingConfig(
        endpoints=[
            listen(
                'orders',
                backpressure=BufferingLimits(high=100, low=20),
                circuit_breaker=CircuitBreakerConfig(minimum_throughput=1),
            ),
        ],
        inbox=InboxConfig(owner_id='test-node:1'),
        transports={'rabbitmq': _StubTransport},
    )
    async with create_test_app(
        imports=[MessagingModule.register(config)],
        providers=[
            object_(unit_of_work, provided_type=IUnitOfWork),
            object_(RecordingAllocator(), provided_type=ISequenceAllocator),
            object_(inbox, provided_type=IInboxStore),
            object_(_durability(inbox, unit_of_work), provided_type=IDurabilityStore),
        ],
    ):
        pass  # wiring builds the listener gate + inbound breaker without error


async def test_inbound_partition_by_without_allocator_raises_at_startup() -> None:
    inbox = FakeInboxStore()
    unit_of_work = RecordingUoW()
    config = MessagingConfig(
        endpoints=[listen('orders', partition_by=_partition_key)],
        inbox=InboxConfig(owner_id='test-node:1'),
        transports={'rabbitmq': _StubTransport},
    )
    with pytest.raises(ImproperlyConfiguredError, match='ISequenceAllocator'):
        async with create_test_app(
            imports=[MessagingModule.register(config)],
            providers=[
                object_(unit_of_work, provided_type=IUnitOfWork),
                object_(inbox, provided_type=IInboxStore),
                object_(_durability(inbox, unit_of_work), provided_type=IDurabilityStore),
            ],
        ):
            pass  # pragma: no cover
