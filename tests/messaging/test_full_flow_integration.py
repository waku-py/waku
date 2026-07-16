from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any, ClassVar
from uuid import uuid4

import anyio
from anyio.lowlevel import checkpoint
from dishka import make_async_container
from typing_extensions import override

from waku import module
from waku.backends.memory._internal.dead_letter import InMemoryDeadLetterStore
from waku.backends.memory._internal.outbox import InMemoryOutboxStore
from waku.di import object_, scoped
from waku.messages import IEvent
from waku.messaging import (
    EndpointDefaults,
    EventHandler,
    IMessageBus,
    IOutgoingMessages,
    IRequest,
    MessagingConfig,
    MessagingExtension,
    MessagingModule,
    OutboxConfig,
    PollingConfig,
    RequestHandler,
    TransactionalBehavior,
    external_endpoint,
    local_queue,
    route,
)
from waku.messaging.config import DeadLetterConfig
from waku.messaging.context import message_context_scope
from waku.messaging.contracts.pipeline import CallNext, IPipelineBehavior
from waku.messaging.durability import IDeadLetterStore, IOutboxStore
from waku.messaging.errors.dead_letter import DeadLetterEntry
from waku.messaging.errors.policy import ErrorPolicy
from waku.messaging.outbox.models import OutboxMessage, OutboxStatus
from waku.messaging.outbox.relay import OutboxRelay, OutboxRelayConfig
from waku.messaging.sequence import ISequenceAllocator
from waku.messaging.transport._internal.wire import encode_payload
from waku.messaging.transport.interfaces import EnvelopeMetadata, IEnvelopeMapper
from waku.testing import create_test_app
from waku.uow import IUnitOfWork

from tests._wait import wait_until
from tests.messaging.helpers import (
    RecordingAllocator,
    RecordingDeadLetterStore,
    RecordingTransport,
    RecordingUoW,
    RelayDepsProvider,
    make_codec,
    make_envelope,
    make_relay_evaluator,
    order_id_partition,
)


@dataclass(frozen=True, slots=True)
class _OrderPlaced(IEvent):
    order_id: str


@dataclass(frozen=True, slots=True)
class _FailingCommand(IRequest[None]):
    value: str


class _OrderPlacedHandler(EventHandler[_OrderPlaced]):
    @override
    async def handle(self, event: _OrderPlaced, /) -> None:
        pass  # pragma: no cover


class _AlwaysFailingHandler(RequestHandler[_FailingCommand, None]):
    @override
    async def handle(self, request: _FailingCommand, /) -> None:
        msg = 'intentional failure'
        raise ValueError(msg)


class _SignalingDeadLetterStore(RecordingDeadLetterStore):
    def __init__(self) -> None:
        super().__init__()
        self.written_event: anyio.Event = anyio.Event()

    @override
    async def save(self, entry: DeadLetterEntry) -> None:
        await super().save(entry)
        self.written_event.set()


class TestEndToEndOutboxFlow:
    @staticmethod
    async def test_publish_to_outbox_then_relay_delivers_to_transport() -> None:
        transport = RecordingTransport()

        config = MessagingConfig(
            endpoints=[external_endpoint('test://notifications')],
            routing=[route(_OrderPlaced).to('test://notifications')],
            outbox=OutboxConfig(),
            transports={'test': RecordingTransport},
            global_pipeline_behaviors=[TransactionalBehavior],
        )

        @module(extensions=[MessagingExtension().bind(_OrderPlacedHandler)])
        class TestModule:
            pass

        async with (
            create_test_app(
                imports=[MessagingModule.register(config), TestModule],
                providers=[
                    object_(RecordingUoW(), provided_type=IUnitOfWork),
                    scoped(IOutboxStore, InMemoryOutboxStore),
                ],
            ) as app,
            app.container() as c,
        ):
            bus = await c.get(IMessageBus)
            await bus.publish(_OrderPlaced(order_id='order-1'))
            outbox = await c.get(IOutboxStore)

        assert isinstance(outbox, InMemoryOutboxStore)
        assert len(outbox.messages) == 1
        assert outbox.messages[0].destination == 'test://notifications'

        relay_config = OutboxRelayConfig(
            polling=PollingConfig(poll_interval_min_seconds=0.01), recovery_interval=timedelta(hours=1)
        )
        async with make_async_container(
            RelayDepsProvider(outbox, transport),
        ) as relay_container:
            relay = OutboxRelay(
                container=relay_container,
                config=relay_config,
                sending_failure_evaluator=make_relay_evaluator(relay_config),
            )
            await relay.start()
            await wait_until(lambda: outbox.messages[0].status == OutboxStatus.DISPATCHED)
            await relay.stop()

        assert len(transport.sent) == 1
        body, destination, _metadata, _mapper = transport.sent[0]
        assert destination == 'notifications'
        assert body == {'order_id': 'order-1'}
        assert outbox.messages[0].status == OutboxStatus.DISPATCHED


class TestErrorPolicyIntegration:
    @staticmethod
    async def test_handler_failure_with_dead_letter_policy() -> None:
        dl_store = _SignalingDeadLetterStore()

        config = MessagingConfig(
            endpoint_defaults=EndpointDefaults(error_policies=(ErrorPolicy.on_any_exception().move_to_dead_letter(),)),
            dead_letter=DeadLetterConfig(),
        )

        async with (
            create_test_app(
                imports=[MessagingModule.register(config)],
                extensions=[MessagingExtension().bind(_AlwaysFailingHandler)],
                providers=[
                    object_(RecordingUoW(), provided_type=IUnitOfWork),
                    object_(dl_store, provided_type=IDeadLetterStore),
                ],
            ) as app,
            app.container() as c,
        ):
            bus = await c.get(IMessageBus)
            await bus.send(_FailingCommand(value='test'))
            with anyio.fail_after(1):
                await dl_store.written_event.wait()

        assert len(dl_store.entries) == 1
        assert isinstance(dl_store.entries[0], DeadLetterEntry)
        assert 'intentional failure' in dl_store.entries[0].error_message


class TestOutboxRelayLifecycleIntegration:
    @staticmethod
    async def test_outbox_relay_starts_and_stops_via_lifecycle_extension() -> None:
        transport = RecordingTransport()
        outbox = InMemoryOutboxStore(InMemoryDeadLetterStore())

        config = MessagingConfig(
            endpoints=[external_endpoint('test://notifications')],
            routing=[route(_OrderPlaced).to('test://notifications')],
            outbox=OutboxConfig(
                relay=OutboxRelayConfig(
                    polling=PollingConfig(poll_interval_min_seconds=0.01), recovery_interval=timedelta(hours=1)
                ),
            ),
            transports={'test': lambda: transport},
            global_pipeline_behaviors=[TransactionalBehavior],
        )

        @module(extensions=[MessagingExtension().bind(_OrderPlacedHandler)])
        class TestModule:
            pass

        async with (
            create_test_app(
                imports=[MessagingModule.register(config), TestModule],
                providers=[
                    object_(RecordingUoW(), provided_type=IUnitOfWork),
                    object_(outbox, provided_type=IOutboxStore),
                ],
            ) as app,
            app.container() as c,
        ):
            bus = await c.get(IMessageBus)
            await bus.publish(_OrderPlaced(order_id='lifecycle-1'))
            await wait_until(lambda: len(transport.sent) == 1)

        assert len(transport.sent) == 1
        body, destination, _metadata, _mapper = transport.sent[0]
        assert destination == 'notifications'
        assert body == {'order_id': 'lifecycle-1'}


# Records whether any send() ever observed an unstarted transport; start() yields repeatedly so an
# already-spawned relay loop gets every chance to run its first tick mid-start — surfacing a
# relay-before-transport ordering race deterministically.
class _SlowStartTransport(RecordingTransport):
    def __init__(self) -> None:
        super().__init__()
        self.started = False
        self.sent_before_started = False

    @override
    async def start(self) -> None:
        for _ in range(50):
            await checkpoint()
        self.started = True

    @override
    async def send(
        self,
        body: dict[str, Any],
        *,
        destination: str,
        metadata: EnvelopeMetadata,
        mapper: IEnvelopeMapper[Any, Any] | None = None,
    ) -> None:
        if not self.started:
            self.sent_before_started = True
        await super().send(body, destination=destination, metadata=metadata, mapper=mapper)


class TestTransportStartupOrdering:
    @staticmethod
    async def test_transport_started_before_relay_first_publish() -> None:
        transport = _SlowStartTransport()
        outbox = InMemoryOutboxStore(InMemoryDeadLetterStore())
        envelope = make_envelope(_OrderPlaced(order_id='early-1'))
        # Staged BEFORE app init: the relay's very first tick already has work to publish.
        outbox.messages.append(
            OutboxMessage(
                id=uuid4(),
                idempotency_key=str(envelope.message_id),
                message_type=envelope.message_type,
                payload=encode_payload(envelope, make_codec()),
                destination='test://notifications',
                correlation_id=envelope.correlation_id,
                causation_id=envelope.causation_id,
            ),
        )

        config = MessagingConfig(
            endpoints=[external_endpoint('test://notifications')],
            routing=[route(_OrderPlaced).to('test://notifications')],
            outbox=OutboxConfig(
                relay=OutboxRelayConfig(
                    polling=PollingConfig(poll_interval_min_seconds=0.01), recovery_interval=timedelta(hours=1)
                ),
            ),
            transports={'test': lambda: transport},
            global_pipeline_behaviors=[TransactionalBehavior],
        )

        @module(extensions=[MessagingExtension().bind(_OrderPlacedHandler)])
        class TestModule:
            pass

        async with create_test_app(
            imports=[MessagingModule.register(config), TestModule],
            providers=[object_(RecordingUoW(), provided_type=IUnitOfWork), object_(outbox, provided_type=IOutboxStore)],
        ):
            await wait_until(lambda: len(transport.sent) == 1)

        assert transport.started
        assert not transport.sent_before_started  # every broker start() completed before the first publish


class TestMessageIdentityPropagation:
    @staticmethod
    async def test_outbox_entry_uses_configured_identity() -> None:
        store = InMemoryOutboxStore(InMemoryDeadLetterStore())
        transport = RecordingTransport()

        config = MessagingConfig(
            endpoints=[external_endpoint('test://orders')],
            routing=[route(_OrderPlaced).to('test://orders')],
            outbox=OutboxConfig(),
            transports={'test': lambda: transport},
            global_pipeline_behaviors=[TransactionalBehavior],
            message_identities={_OrderPlaced: 'order-placed'},
        )

        async with (
            create_test_app(
                imports=[MessagingModule.register(config)],
                extensions=[MessagingExtension().bind(_OrderPlacedHandler)],
                providers=[
                    object_(RecordingUoW(), provided_type=IUnitOfWork),
                    object_(store, provided_type=IOutboxStore),
                ],
            ) as app,
            app.container() as c,
        ):
            bus = await c.get(IMessageBus)
            await bus.publish(_OrderPlaced(order_id='o-1'))

        assert len(store.messages) == 1
        assert store.messages[0].message_type == 'order-placed'

    @staticmethod
    async def test_outbox_entry_falls_back_to_fqn_without_identity_config() -> None:
        store = InMemoryOutboxStore(InMemoryDeadLetterStore())
        transport = RecordingTransport()

        config = MessagingConfig(
            endpoints=[external_endpoint('test://orders')],
            routing=[route(_OrderPlaced).to('test://orders')],
            outbox=OutboxConfig(),
            transports={'test': lambda: transport},
            global_pipeline_behaviors=[TransactionalBehavior],
        )

        async with (
            create_test_app(
                imports=[MessagingModule.register(config)],
                extensions=[MessagingExtension().bind(_OrderPlacedHandler)],
                providers=[
                    object_(RecordingUoW(), provided_type=IUnitOfWork),
                    object_(store, provided_type=IOutboxStore),
                ],
            ) as app,
            app.container() as c,
        ):
            bus = await c.get(IMessageBus)
            await bus.publish(_OrderPlaced(order_id='o-2'))

        expected_fqn = f'{_OrderPlaced.__module__}.{_OrderPlaced.__qualname__}'
        assert store.messages[0].message_type == expected_fqn


def _partitioned_outbox_row(
    *,
    group_id: str,
    sequence_number: int,
    order_id: str,
) -> OutboxMessage:
    envelope = make_envelope(_OrderPlaced(order_id=order_id))
    return OutboxMessage(
        id=uuid4(),
        idempotency_key=str(envelope.message_id),
        message_type=envelope.message_type,
        payload=encode_payload(envelope, make_codec()),
        destination='test://orders',
        correlation_id=envelope.correlation_id,
        causation_id=envelope.causation_id,
        group_id=group_id,
        sequence_number=sequence_number,
    )


class TestRelayPartitionOrdering:
    @staticmethod
    async def test_relay_dispatches_group_heads_in_sequence_order() -> None:
        transport = RecordingTransport()
        store = InMemoryOutboxStore(InMemoryDeadLetterStore())
        # Staged OUT of sequence order: the relay must still dispatch A-1, A-2, A-3 because it claims
        # the head (lowest pending sequence) of the group each poll — not whatever was inserted first.
        # If the relay used FIFO fetch this would dispatch A-2, A-1, A-3 and the assert would fail.
        store.messages.extend([
            _partitioned_outbox_row(group_id='A', sequence_number=2, order_id='A-2'),
            _partitioned_outbox_row(group_id='A', sequence_number=1, order_id='A-1'),
            _partitioned_outbox_row(group_id='A', sequence_number=3, order_id='A-3'),
        ])

        relay_config = OutboxRelayConfig(
            polling=PollingConfig(poll_interval_min_seconds=0.01), recovery_interval=timedelta(hours=1)
        )
        async with make_async_container(RelayDepsProvider(store, transport)) as container:
            relay = OutboxRelay(
                container=container,
                config=relay_config,
                sending_failure_evaluator=make_relay_evaluator(relay_config),
            )
            await relay.start()
            await wait_until(lambda: sum(1 for m in store.messages if m.status == OutboxStatus.DISPATCHED) == 3)
            await relay.stop()

        dispatched_order = [body['order_id'] for body, _destination, _metadata, _mapper in transport.sent]
        assert dispatched_order == ['A-1', 'A-2', 'A-3']


class _ClassVarRetryHandler(EventHandler[_OrderPlaced]):
    attempts: ClassVar[list[int]] = []
    error_policies = (ErrorPolicy.on_exception(RuntimeError).retry(max_attempts=3),)

    @override
    async def handle(self, event: _OrderPlaced, /) -> None:
        self.attempts.append(1)
        msg = 'boom'
        raise RuntimeError(msg)


class _DefaultFallbackHandler(EventHandler[_OrderPlaced]):
    attempts: ClassVar[list[int]] = []

    @override
    async def handle(self, event: _OrderPlaced, /) -> None:
        self.attempts.append(1)
        msg = 'boom'
        raise RuntimeError(msg)


class _RecordingBehavior(IPipelineBehavior[Any, Any]):
    seen: ClassVar[list[str]] = []

    @override
    async def handle(self, message: Any, /, call_next: CallNext[Any]) -> Any:
        self.seen.append(type(message).__name__)
        return await call_next()


class _BehaviorHandler(EventHandler[_OrderPlaced]):
    handled: ClassVar[list[str]] = []
    behaviors = (_RecordingBehavior,)

    @override
    async def handle(self, event: _OrderPlaced, /) -> None:
        self.handled.append(event.order_id)


class TestClassVarHandlerConfig:
    @staticmethod
    async def test_classvar_error_policy_is_applied_when_handler_raises() -> None:
        _ClassVarRetryHandler.attempts.clear()
        config = MessagingConfig(
            endpoints=[local_queue('orders')],
            routing=[route(_OrderPlaced).to('orders')],
        )
        async with (
            create_test_app(
                imports=[MessagingModule.register(config)],
                extensions=[MessagingExtension().bind(_ClassVarRetryHandler)],
            ) as app,
            app.container() as container,
        ):
            bus = await container.get(IMessageBus)
            await bus.publish(_OrderPlaced(order_id='o-3'))

        assert len(_ClassVarRetryHandler.attempts) == 3

    @staticmethod
    async def test_default_policy_applies_when_handler_declares_none() -> None:
        _DefaultFallbackHandler.attempts.clear()
        config = MessagingConfig(
            endpoints=[local_queue('orders')],
            routing=[route(_OrderPlaced).to('orders')],
            endpoint_defaults=EndpointDefaults(error_policies=(ErrorPolicy.on_any_exception().retry(max_attempts=2),)),
        )
        async with (
            create_test_app(
                imports=[MessagingModule.register(config)],
                extensions=[MessagingExtension().bind(_DefaultFallbackHandler)],
            ) as app,
            app.container() as container,
        ):
            bus = await container.get(IMessageBus)
            await bus.publish(_OrderPlaced(order_id='o-4'))

        assert len(_DefaultFallbackHandler.attempts) == 2

    @staticmethod
    async def test_additional_behavior_classvar_runs_around_handler() -> None:
        _RecordingBehavior.seen.clear()
        _BehaviorHandler.handled.clear()
        config = MessagingConfig(
            endpoints=[local_queue('orders')],
            routing=[route(_OrderPlaced).to('orders')],
        )
        async with (
            create_test_app(
                imports=[MessagingModule.register(config)],
                extensions=[MessagingExtension().bind(_BehaviorHandler)],
            ) as app,
            app.container() as container,
        ):
            bus = await container.get(IMessageBus)
            await bus.publish(_OrderPlaced(order_id='o-5'))

        assert _RecordingBehavior.seen == ['_OrderPlaced']
        assert _BehaviorHandler.handled == ['o-5']


@dataclass(frozen=True, slots=True)
class _ShipOrder(IRequest[None]):
    order_id: str


@dataclass(frozen=True, slots=True)
class _OrderShipped(IEvent):
    order_id: str


class _ShipOrderHandler(RequestHandler[_ShipOrder, None]):
    def __init__(self, outgoing: IOutgoingMessages) -> None:
        self._outgoing = outgoing

    @override
    async def handle(self, request: _ShipOrder, /) -> None:
        self._outgoing.publish(_OrderShipped(order_id=request.order_id))


class _OrderShippedHandler(EventHandler[_OrderShipped]):
    # Bound only to satisfy route() validation; _OrderShipped is routed to an external endpoint
    # (outbox), so this local handler is never invoked.
    @override
    async def handle(self, event: _OrderShipped, /) -> None:
        pass  # pragma: no cover


class TestGroupIdPropagation:
    @staticmethod
    async def test_cascaded_message_inherits_parent_group_id_via_context() -> None:
        # partition_by is deliberately NOT set: the ONLY way the cascaded _OrderShipped outbox row can
        # carry group_id='order-9' is propagation parent-context -> _create_envelope -> cascade envelope.
        outbox = InMemoryOutboxStore(InMemoryDeadLetterStore())
        config = MessagingConfig(
            endpoints=[external_endpoint('test://shipped')],
            routing=[route(_OrderShipped).to('test://shipped')],
            outbox=OutboxConfig(),
            transports={'test': RecordingTransport},
            global_pipeline_behaviors=[TransactionalBehavior],
        )

        @module(
            extensions=[
                MessagingExtension().bind(_ShipOrderHandler).bind(_OrderShippedHandler),
            ],
        )
        class TestModule:
            pass

        async with (
            create_test_app(
                imports=[MessagingModule.register(config), TestModule],
                providers=[
                    object_(RecordingUoW(), provided_type=IUnitOfWork),
                    object_(RecordingAllocator(), provided_type=ISequenceAllocator),
                    object_(outbox, provided_type=IOutboxStore),
                ],
            ) as app,
            app.container() as c,
        ):
            bus = await c.get(IMessageBus)
            parent = make_envelope(_ShipOrder(order_id='order-9'), group_id='order-9')
            with message_context_scope(parent):
                await bus.invoke(_ShipOrder(order_id='order-9'))

        assert len(outbox.messages) == 1
        assert outbox.messages[0].group_id == 'order-9'
        assert outbox.messages[0].sequence_number == 1


class TestPartitionOrderingEndToEnd:
    @staticmethod
    async def test_concurrent_groups_each_dispatched_in_strict_sequence_order() -> None:
        transport = RecordingTransport()
        outbox = InMemoryOutboxStore(InMemoryDeadLetterStore())
        config = MessagingConfig(
            endpoints=[external_endpoint('test://orders', partition_by=order_id_partition)],
            routing=[route(_OrderPlaced).to('test://orders')],
            outbox=OutboxConfig(
                relay=OutboxRelayConfig(
                    polling=PollingConfig(poll_interval_min_seconds=0.01), recovery_interval=timedelta(hours=1)
                ),
            ),
            transports={'test': lambda: transport},
            global_pipeline_behaviors=[TransactionalBehavior],
        )

        @module(extensions=[MessagingExtension().bind(_OrderPlacedHandler)])
        class TestModule:
            pass

        async with (
            create_test_app(
                imports=[MessagingModule.register(config), TestModule],
                providers=[
                    object_(RecordingUoW(), provided_type=IUnitOfWork),
                    object_(RecordingAllocator(), provided_type=ISequenceAllocator),
                    object_(outbox, provided_type=IOutboxStore),
                ],
            ) as app,
            app.container() as c,
        ):
            bus = await c.get(IMessageBus)
            for order_id in ('A', 'B'):
                for _ in range(3):
                    await bus.publish(_OrderPlaced(order_id=order_id))
            await wait_until(lambda: len(transport.sent) >= 6)

        by_idempotency_key = {m.idempotency_key: m for m in outbox.messages}
        per_group: dict[str, list[int]] = {}
        for _body, _destination, metadata, _mapper in transport.sent:
            row = by_idempotency_key[metadata.message_id]
            assert row.group_id is not None
            assert row.sequence_number is not None
            per_group.setdefault(row.group_id, []).append(row.sequence_number)

        # Each group dispatched strictly seq 1, 2, 3 in order; groups run in parallel (relay claims one
        # head per group per poll, advancing each group independently).
        assert per_group == {'A': [1, 2, 3], 'B': [1, 2, 3]}


class TestMultiDestinationFanOut:
    @staticmethod
    async def test_event_routed_to_two_endpoints_persists_and_delivers_to_both() -> None:
        transport_ta = RecordingTransport()
        transport_tb = RecordingTransport()
        outbox = InMemoryOutboxStore(InMemoryDeadLetterStore())
        config = MessagingConfig(
            endpoints=[external_endpoint('ta://events'), external_endpoint('tb://events')],
            routing=[route(_OrderPlaced).to('ta://events'), route(_OrderPlaced).to('tb://events')],
            outbox=OutboxConfig(
                relay=OutboxRelayConfig(
                    polling=PollingConfig(poll_interval_min_seconds=0.01), recovery_interval=timedelta(hours=1)
                ),
            ),
            transports={'ta': lambda: transport_ta, 'tb': lambda: transport_tb},
            global_pipeline_behaviors=[TransactionalBehavior],
        )

        @module(extensions=[MessagingExtension().bind(_OrderPlacedHandler)])
        class TestModule:
            pass

        async with (
            create_test_app(
                imports=[MessagingModule.register(config), TestModule],
                providers=[
                    object_(RecordingUoW(), provided_type=IUnitOfWork),
                    object_(RecordingAllocator(), provided_type=ISequenceAllocator),
                    object_(outbox, provided_type=IOutboxStore),
                ],
            ) as app,
            app.container() as c,
        ):
            bus = await c.get(IMessageBus)
            await bus.publish(_OrderPlaced(order_id='x'))
            await wait_until(lambda: len(transport_ta.sent) >= 1 and len(transport_tb.sent) >= 1)

        assert len(outbox.messages) == 2
        assert {m.destination for m in outbox.messages} == {'ta://events', 'tb://events'}
        assert len({m.idempotency_key for m in outbox.messages}) == 1
        ta_body, *_ta_rest = transport_ta.sent[0]
        tb_body, *_tb_rest = transport_tb.sent[0]
        assert ta_body['order_id'] == 'x'
        assert tb_body['order_id'] == 'x'
