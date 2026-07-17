from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import ClassVar

import anyio
import pytest
from typing_extensions import override

from waku.di import object_, scoped, singleton
from waku.exceptions import ImproperlyConfiguredError
from waku.messages import IEvent
from waku.messaging import (
    EndpointDefaults,
    IMessageBus,
    InboxConfig,
    MessagingConfig,
    MessagingExtension,
    MessagingModule,
    TransactionalBehavior,
)
from waku.messaging.config import DeadLetterConfig
from waku.messaging.durability import IDeadLetterStore, IDurabilityStore, IInboxStore
from waku.messaging.endpoints.base import EndpointMode
from waku.messaging.errors.policy import ErrorPolicy
from waku.messaging.handler import EventHandler
from waku.messaging.inbox import InboxStatus
from waku.messaging.router import local_queue, route
from waku.messaging.sequence import ISequenceAllocator
from waku.serialization.codec import PayloadCodec
from waku.testing import create_test_app
from waku.uow import IUnitOfWork

from tests._wait import wait_until
from tests.messaging.helpers import (
    EndpointOnlyObserver,
    EndpointSink,
    RecordingAllocator,
    RecordingDeadLetterStore,
    RecordingUoW,
    durability_for_inbox,
    durability_for_inbox_and_dead_letters,
    make_envelope,
    make_inbox_entry,
)
from tests.messaging.inbox.fake_store import FakeInboxStore


@dataclass(frozen=True, slots=True)
class _OrderPlaced(IEvent):
    order_id: str


class _RecordingHandler(EventHandler[_OrderPlaced]):
    observed: ClassVar[list[str]] = []

    @override
    async def handle(self, message: _OrderPlaced, /) -> None:
        self.observed.append(message.order_id)


class _SecondRecordingHandler(EventHandler[_OrderPlaced]):
    observed: ClassVar[list[str]] = []

    @override
    async def handle(self, message: _OrderPlaced, /) -> None:
        self.observed.append(message.order_id)


# Inbox-only config (no outbox, no dead_letter_store): also exercises that PayloadCodec is
# registered for inbox-only setups (the durable endpoint encodes the payload before persisting).
def _durable_config() -> MessagingConfig:
    return MessagingConfig(
        endpoints=[
            local_queue(
                'orders', mode=EndpointMode.DURABLE, stop_timeout=timedelta(seconds=1.0), max_buffer_size=math.inf
            )
        ],
        routing=[route(_OrderPlaced).to('orders')],
        inbox=InboxConfig(owner_id='test-node:1'),
        global_pipeline_behaviors=[TransactionalBehavior],
    )


class _FailingOrderHandler(EventHandler[_OrderPlaced]):
    @override
    async def handle(self, message: _OrderPlaced, /) -> None:
        msg = 'handler always fails'
        raise RuntimeError(msg)


# Move-to-dead-letter config: the handler always fails, so the on_any_exception move_to_dead_letter
# policy dead-letters the message and clears its inbox row through the atomic inbox move.
def _dead_letter_config() -> MessagingConfig:
    return MessagingConfig(
        endpoints=[
            local_queue(
                'orders', mode=EndpointMode.DURABLE, stop_timeout=timedelta(seconds=1.0), max_buffer_size=math.inf
            )
        ],
        routing=[route(_OrderPlaced).to('orders')],
        inbox=InboxConfig(owner_id='test-node:1'),
        endpoint_defaults=EndpointDefaults(error_policies=(ErrorPolicy.on_any_exception().move_to_dead_letter(),)),
        dead_letter=DeadLetterConfig(),
        global_pipeline_behaviors=[TransactionalBehavior],
    )


class TestDurableInboxIntegration:
    @staticmethod
    async def test_message_is_persisted_and_handled() -> None:
        _RecordingHandler.observed = []
        inbox = FakeInboxStore()
        async with (
            create_test_app(
                imports=[MessagingModule.register(_durable_config())],
                extensions=[MessagingExtension().bind(_RecordingHandler)],
                providers=[
                    object_(RecordingUoW(), provided_type=IUnitOfWork),
                    object_(inbox, provided_type=IInboxStore),
                    object_(RecordingAllocator(), provided_type=ISequenceAllocator),
                    scoped(IDurabilityStore, durability_for_inbox),
                ],
            ) as app,
            app.container() as container,
        ):
            bus = await container.get(IMessageBus)
            await bus.publish(_OrderPlaced(order_id='o-1'))

        entries = list(inbox.entries.values())
        assert len(entries) == 1
        assert entries[0].status is InboxStatus.HANDLED
        assert _RecordingHandler.observed == ['o-1']

    @staticmethod
    async def test_distinct_publishes_each_persist_and_handle() -> None:
        # Two distinct envelopes → two inbox rows. Same-message_id dedup is proven in test_receiver.py.
        _RecordingHandler.observed = []
        inbox = FakeInboxStore()
        async with (
            create_test_app(
                imports=[MessagingModule.register(_durable_config())],
                extensions=[MessagingExtension().bind(_RecordingHandler)],
                providers=[
                    object_(RecordingUoW(), provided_type=IUnitOfWork),
                    object_(inbox, provided_type=IInboxStore),
                    object_(RecordingAllocator(), provided_type=ISequenceAllocator),
                    scoped(IDurabilityStore, durability_for_inbox),
                ],
            ) as app,
            app.container() as container,
        ):
            bus = await container.get(IMessageBus)
            await bus.publish(_OrderPlaced(order_id='o-2'))
            await bus.publish(_OrderPlaced(order_id='o-2'))

        assert len(inbox.entries) == 2
        assert _RecordingHandler.observed.count('o-2') == 2

    @staticmethod
    async def test_fan_out_two_handlers_one_durable_endpoint() -> None:
        _RecordingHandler.observed = []
        _SecondRecordingHandler.observed = []
        inbox = FakeInboxStore()

        def handled_count() -> int:
            return sum(1 for entry in inbox.entries.values() if entry.status is InboxStatus.HANDLED)

        async with (
            create_test_app(
                imports=[MessagingModule.register(_durable_config())],
                extensions=[
                    MessagingExtension().bind(_RecordingHandler, _SecondRecordingHandler),
                ],
                providers=[
                    object_(RecordingUoW(), provided_type=IUnitOfWork),
                    object_(inbox, provided_type=IInboxStore),
                    object_(RecordingAllocator(), provided_type=ISequenceAllocator),
                    scoped(IDurabilityStore, durability_for_inbox),
                ],
            ) as app,
            app.container() as container,
        ):
            bus = await container.get(IMessageBus)
            await bus.publish(_OrderPlaced(order_id='fan-1'))
            await wait_until(lambda: handled_count() == 2)

            assert len(inbox.entries) == 2
            assert _RecordingHandler.observed == ['fan-1']
            assert _SecondRecordingHandler.observed == ['fan-1']

            purged = await inbox.delete_expired_handled(datetime.now(tz=UTC) + timedelta(minutes=10))
            assert purged == 2

    @staticmethod
    async def test_failing_durable_handler_dead_letters_message_and_clears_inbox_row() -> None:
        # A durable handler that always fails resolves to move_to_dead_letter: the atomic inbox move
        # writes the message to the store's own dead-letter facet (inbox.dead_letters) and removes its
        # inbox row in one transaction, leaving the standalone sink untouched. (A FAILED move's
        # INCOMING-row retention — ERR-2 — is proven against the rollback-modelling workspace store in
        # tests/messaging/endpoints/test_durable_inbox_receiver.py.)
        inbox = FakeInboxStore()
        standalone_dlq = RecordingDeadLetterStore()
        async with (
            create_test_app(
                imports=[MessagingModule.register(_dead_letter_config())],
                extensions=[MessagingExtension().bind(_FailingOrderHandler)],
                providers=[
                    object_(RecordingUoW(), provided_type=IUnitOfWork),
                    object_(inbox, provided_type=IInboxStore),
                    object_(RecordingAllocator(), provided_type=ISequenceAllocator),
                    object_(standalone_dlq, provided_type=IDeadLetterStore),
                    scoped(IDurabilityStore, durability_for_inbox_and_dead_letters),
                ],
            ) as app,
            app.container() as container,
        ):
            bus = await container.get(IMessageBus)
            await bus.publish(_OrderPlaced(order_id='o-dlq'))
            await wait_until(lambda: len(inbox.dead_letters.entries) == 1)

        assert inbox.entries == {}  # the row was moved to the dead-letter facet, not left behind
        assert len(inbox.dead_letters.entries) == 1
        assert standalone_dlq.entries == []  # durable path uses the atomic inbox move, not the standalone sink

    @staticmethod
    async def test_global_durable_default_makes_unset_local_queue_durable() -> None:
        _RecordingHandler.observed = []
        inbox = FakeInboxStore()
        config = MessagingConfig(
            endpoints=[
                local_queue('orders', stop_timeout=timedelta(seconds=1.0))
            ],  # mode unset -> inherits the global default
            routing=[route(_OrderPlaced).to('orders')],
            inbox=InboxConfig(owner_id='test-node:1'),
            global_pipeline_behaviors=[TransactionalBehavior],
            endpoint_defaults=EndpointDefaults(mode=EndpointMode.DURABLE),
        )
        async with (
            create_test_app(
                imports=[MessagingModule.register(config)],
                extensions=[MessagingExtension().bind(_RecordingHandler)],
                providers=[
                    object_(RecordingUoW(), provided_type=IUnitOfWork),
                    object_(inbox, provided_type=IInboxStore),
                    object_(RecordingAllocator(), provided_type=ISequenceAllocator),
                    scoped(IDurabilityStore, durability_for_inbox),
                ],
            ) as app,
            app.container() as container,
        ):
            bus = await container.get(IMessageBus)
            await bus.publish(_OrderPlaced(order_id='g-1'))

        # Inbox row proves the endpoint went durable (BUFFERED never writes inbox).
        entries = list(inbox.entries.values())
        assert len(entries) == 1
        assert entries[0].status is InboxStatus.HANDLED
        assert _RecordingHandler.observed == ['g-1']

    @staticmethod
    async def test_explicit_buffered_mode_survives_global_durable_default() -> None:
        _RecordingHandler.observed = []
        inbox = FakeInboxStore()
        config = MessagingConfig(
            endpoints=[local_queue('orders', mode=EndpointMode.BUFFERED, stop_timeout=timedelta(seconds=1.0))],
            routing=[route(_OrderPlaced).to('orders')],
            inbox=InboxConfig(owner_id='test-node:1'),
            global_pipeline_behaviors=[TransactionalBehavior],
            endpoint_defaults=EndpointDefaults(mode=EndpointMode.DURABLE),
        )
        async with (
            create_test_app(
                imports=[MessagingModule.register(config)],
                extensions=[MessagingExtension().bind(_RecordingHandler)],
                providers=[
                    object_(RecordingUoW(), provided_type=IUnitOfWork),
                    object_(inbox, provided_type=IInboxStore),
                    object_(RecordingAllocator(), provided_type=ISequenceAllocator),
                    scoped(IDurabilityStore, durability_for_inbox),
                ],
            ) as app,
            app.container() as container,
        ):
            bus = await container.get(IMessageBus)
            await bus.publish(_OrderPlaced(order_id='b-1'))
            await wait_until(lambda: _RecordingHandler.observed == ['b-1'])

        # Explicit per-endpoint mode wins over the global default; inbox untouched (BUFFERED).
        assert inbox.entries == {}

    @staticmethod
    def test_global_durable_default_without_inbox_is_rejected() -> None:
        config = MessagingConfig(
            endpoints=[local_queue('orders')],  # mode unset -> durable under the global default
            routing=[route(_OrderPlaced).to('orders')],
            endpoint_defaults=EndpointDefaults(mode=EndpointMode.DURABLE),
        )
        with pytest.raises(ImproperlyConfiguredError):
            MessagingModule.register(config)

    @staticmethod
    def test_inline_endpoint_with_requeue_policy_is_rejected() -> None:
        config = MessagingConfig(
            endpoints=[local_queue('orders', mode=EndpointMode.INLINE)],
            routing=[route(_OrderPlaced).to('orders')],
            endpoint_defaults=EndpointDefaults(error_policies=(ErrorPolicy.on_any_exception().requeue(),)),
        )
        with pytest.raises(ImproperlyConfiguredError, match='INLINE'):
            MessagingModule.register(config)

    @staticmethod
    def test_inline_endpoint_with_pause_policy_is_rejected() -> None:
        config = MessagingConfig(
            endpoints=[local_queue('orders', mode=EndpointMode.INLINE)],
            routing=[route(_OrderPlaced).to('orders')],
            endpoint_defaults=EndpointDefaults(
                error_policies=(ErrorPolicy.on_any_exception().pause_processing(timedelta(minutes=5)),),
            ),
        )
        with pytest.raises(ImproperlyConfiguredError, match='INLINE'):
            MessagingModule.register(config)

    @staticmethod
    async def test_inline_endpoint_with_per_handler_requeue_policy_is_rejected() -> None:
        # Default-policy guard can't see per-handler policies; post-merge _finalize must catch this.
        class _RequeueHandler(EventHandler[_OrderPlaced]):
            error_policies: ClassVar = (ErrorPolicy.on_any_exception().requeue(),)

            @override
            async def handle(self, event: _OrderPlaced, /) -> None: ...  # pragma: no cover - rejected at build

        config = MessagingConfig(
            endpoints=[local_queue('orders', mode=EndpointMode.INLINE)],
            routing=[route(_OrderPlaced).to('orders')],
        )
        with pytest.raises(ImproperlyConfiguredError, match='INLINE'):
            async with create_test_app(
                imports=[MessagingModule.register(config)],
                extensions=[MessagingExtension().bind(_RequeueHandler)],
            ):
                pass

    @staticmethod
    async def test_recovery_drain_applies_execution_timeout_to_durable_handler() -> None:
        # Recovery executor must honour endpoint_defaults.execution_timeout like the live path. If it doesn't,
        # the drain blocks forever and the DLQ row never appears (wait_until trips). The atomic
        # move_to_dead_letter writes to the inbox store's own dead-letter facet (inbox.dead_letters), not
        # the standalone IDeadLetterStore sink — which stays empty on the durable path.
        inbox = FakeInboxStore()
        standalone_dlq = RecordingDeadLetterStore()
        config = MessagingConfig(
            endpoints=[local_queue('orders', mode=EndpointMode.DURABLE, stop_timeout=timedelta(seconds=1.0))],
            routing=[route(_OrderPlaced).to('orders')],
            inbox=InboxConfig(owner_id='node-a:1', recovery_interval=timedelta(seconds=0.01)),
            dead_letter=DeadLetterConfig(),
            endpoint_defaults=EndpointDefaults(
                error_policies=(ErrorPolicy.on_any_exception().move_to_dead_letter(),),
                execution_timeout=timedelta(seconds=0.01),
            ),
            global_pipeline_behaviors=[TransactionalBehavior],
        )
        blocked = anyio.Event()  # never set: the handler stalls until the deadline cancels it

        class _BlockingHandler(EventHandler[_OrderPlaced]):
            @override
            async def handle(self, message: _OrderPlaced, /) -> None:
                await blocked.wait()

        async with create_test_app(
            imports=[MessagingModule.register(config)],
            extensions=[MessagingExtension().bind(_BlockingHandler)],
            providers=[
                object_(RecordingUoW(), provided_type=IUnitOfWork),
                object_(inbox, provided_type=IInboxStore),
                object_(standalone_dlq, provided_type=IDeadLetterStore),
                object_(RecordingAllocator(), provided_type=ISequenceAllocator),
                scoped(IDurabilityStore, durability_for_inbox_and_dead_letters),
            ],
        ) as app:
            codec = await app.container.get(PayloadCodec)
            envelope = make_envelope(_OrderPlaced(order_id='rec-1'))
            entry = make_inbox_entry(envelope, _BlockingHandler, codec=codec)
            inbox.entries[entry.id, entry.destination] = entry
            await wait_until(lambda: len(inbox.dead_letters.entries) == 1)

        assert len(inbox.dead_letters.entries) == 1
        assert standalone_dlq.entries == []  # durable path uses the atomic inbox move, not the standalone sink

    @staticmethod
    async def test_recovery_drain_discards_expired_durable_message() -> None:
        # Receive-time expiry on the recovery path: an expired INCOMING row is discarded (row deleted),
        # the handler never runs, and it is NOT dead-lettered (expiry is intended, not a failure).
        _RecordingHandler.observed = []
        inbox = FakeInboxStore()
        dlq = RecordingDeadLetterStore()
        config = MessagingConfig(
            endpoints=[local_queue('orders', mode=EndpointMode.DURABLE, stop_timeout=timedelta(seconds=1.0))],
            routing=[route(_OrderPlaced).to('orders')],
            inbox=InboxConfig(owner_id='node-a:1', recovery_interval=timedelta(seconds=0.01)),
            dead_letter=DeadLetterConfig(),
            global_pipeline_behaviors=[TransactionalBehavior],
        )
        async with create_test_app(
            imports=[MessagingModule.register(config)],
            extensions=[MessagingExtension().bind(_RecordingHandler)],
            providers=[
                object_(RecordingUoW(), provided_type=IUnitOfWork),
                object_(inbox, provided_type=IInboxStore),
                object_(dlq, provided_type=IDeadLetterStore),
                object_(RecordingAllocator(), provided_type=ISequenceAllocator),
                scoped(IDurabilityStore, durability_for_inbox_and_dead_letters),
            ],
        ) as app:
            codec = await app.container.get(PayloadCodec)
            envelope = make_envelope(
                _OrderPlaced(order_id='exp-1'),
                expires_at=datetime.now(tz=UTC) - timedelta(hours=1),
            )
            entry = make_inbox_entry(envelope, _RecordingHandler, codec=codec)
            inbox.entries[entry.id, entry.destination] = entry
            await wait_until(lambda: (entry.id, entry.destination) not in inbox.entries)

        assert (entry.id, entry.destination) not in inbox.entries  # DISCARDED → delete, no leaked row
        assert _RecordingHandler.observed == []  # handler never ran
        assert dlq.entries == []  # expiry is not a failure → never dead-lettered

    @staticmethod
    async def test_recovery_drain_fires_endpoint_declared_observer_for_source_uri() -> None:
        # Crash recovery must fire the SAME per-endpoint observers as the live path — the drainer's
        # executor is keyed to entry.source_uri, and ObserverPlan.for_endpoint composes on that URI.
        _RecordingHandler.observed = []
        inbox = FakeInboxStore()
        config = MessagingConfig(
            endpoints=[
                local_queue(
                    'orders',
                    mode=EndpointMode.DURABLE,
                    stop_timeout=timedelta(seconds=1.0),
                    observers=(EndpointOnlyObserver,),
                )
            ],
            routing=[route(_OrderPlaced).to('orders')],
            inbox=InboxConfig(owner_id='node-a:1', recovery_interval=timedelta(seconds=0.01)),
            global_pipeline_behaviors=[TransactionalBehavior],
        )
        async with create_test_app(
            imports=[MessagingModule.register(config)],
            extensions=[MessagingExtension().bind(_RecordingHandler)],
            providers=[
                object_(RecordingUoW(), provided_type=IUnitOfWork),
                object_(inbox, provided_type=IInboxStore),
                object_(RecordingAllocator(), provided_type=ISequenceAllocator),
                scoped(IDurabilityStore, durability_for_inbox),
                singleton(EndpointSink),
            ],
        ) as app:
            sink = await app.container.get(EndpointSink)
            codec = await app.container.get(PayloadCodec)
            envelope = make_envelope(_OrderPlaced(order_id='recovered-obs'))
            entry = make_inbox_entry(envelope, _RecordingHandler, codec=codec)
            inbox.entries[entry.id, entry.destination] = entry
            await wait_until(lambda: sink.events == [('executing', 'orders'), ('executed', 'orders')])

        assert sink.events == [('executing', 'orders'), ('executed', 'orders')]

    @staticmethod
    async def test_scheduled_poll_promotes_due_row_then_drain_runs_the_handler() -> None:
        # End-to-end wiring of the dedicated scheduled poll: a due SCHEDULED row is promoted to INCOMING
        # and the recovery drain then runs the handler. (Promotion ordering vs immediate siblings is
        # pinned deterministically by the promote_due_scheduled contract suite.)
        _RecordingHandler.observed = []
        inbox = FakeInboxStore()
        config = MessagingConfig(
            endpoints=[local_queue('orders', mode=EndpointMode.DURABLE, stop_timeout=timedelta(seconds=1.0))],
            routing=[route(_OrderPlaced).to('orders')],
            inbox=InboxConfig(
                owner_id='node-a:1',
                recovery_interval=timedelta(seconds=0.01),
                scheduled_poll_interval=timedelta(seconds=0.01),
            ),
            global_pipeline_behaviors=[TransactionalBehavior],
        )
        async with create_test_app(
            imports=[MessagingModule.register(config)],
            extensions=[MessagingExtension().bind(_RecordingHandler)],
            providers=[
                object_(RecordingUoW(), provided_type=IUnitOfWork),
                object_(inbox, provided_type=IInboxStore),
                object_(RecordingAllocator(), provided_type=ISequenceAllocator),
                scoped(IDurabilityStore, durability_for_inbox),
            ],
        ) as app:
            codec = await app.container.get(PayloadCodec)
            envelope = make_envelope(_OrderPlaced(order_id='sched-1'))
            entry = make_inbox_entry(
                envelope,
                _RecordingHandler,
                codec=codec,
                status=InboxStatus.SCHEDULED,
                execution_time=datetime.now(tz=UTC) - timedelta(hours=1),  # already due
            )
            inbox.entries[entry.id, entry.destination] = entry
            await wait_until(lambda: _RecordingHandler.observed == ['sched-1'])

        assert _RecordingHandler.observed == ['sched-1']

    @staticmethod
    async def test_scheduled_poll_promotes_keyless_row_without_invoking_the_allocator() -> None:
        # Keyless scheduled messages (group_id=None) promote and drain without ever calling
        # allocate — the backend-provided allocator is resolved each tick but stays un-invoked.
        _RecordingHandler.observed = []
        inbox = FakeInboxStore()
        allocator = RecordingAllocator()
        config = MessagingConfig(
            endpoints=[local_queue('orders', mode=EndpointMode.DURABLE, stop_timeout=timedelta(seconds=1.0))],
            routing=[route(_OrderPlaced).to('orders')],
            inbox=InboxConfig(
                owner_id='node-a:1',
                recovery_interval=timedelta(seconds=0.01),
                scheduled_poll_interval=timedelta(seconds=0.01),
            ),
            global_pipeline_behaviors=[TransactionalBehavior],
        )
        async with create_test_app(
            imports=[MessagingModule.register(config)],
            extensions=[MessagingExtension().bind(_RecordingHandler)],
            providers=[
                object_(RecordingUoW(), provided_type=IUnitOfWork),
                object_(inbox, provided_type=IInboxStore),
                object_(allocator, provided_type=ISequenceAllocator),
                scoped(IDurabilityStore, durability_for_inbox),
            ],
        ) as app:
            codec = await app.container.get(PayloadCodec)
            envelope = make_envelope(_OrderPlaced(order_id='keyless-sched'))
            entry = make_inbox_entry(
                envelope,
                _RecordingHandler,
                codec=codec,
                status=InboxStatus.SCHEDULED,
                execution_time=datetime.now(tz=UTC) - timedelta(hours=1),  # already due, keyless (group_id=None)
            )
            inbox.entries[entry.id, entry.destination] = entry
            await wait_until(lambda: _RecordingHandler.observed == ['keyless-sched'])

        assert _RecordingHandler.observed == ['keyless-sched']
        assert allocator.calls == []
