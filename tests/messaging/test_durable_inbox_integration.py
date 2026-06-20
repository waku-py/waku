from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import ClassVar

import anyio
import pytest
from typing_extensions import override

from waku.di import object_
from waku.exceptions import ImproperlyConfiguredError
from waku.messaging import (
    IMessageBus,
    InboxConfig,
    InboxStatus,
    MessagingConfig,
    MessagingExtension,
    MessagingModule,
    TransactionalBehavior,
)
from waku.messaging._identifiers import EndpointUri  # noqa: PLC2701
from waku.messaging.config import DeadLetterConfig
from waku.messaging.contracts.event import IEvent
from waku.messaging.endpoints.base import EndpointMode, local_queue
from waku.messaging.errors.policy import ErrorPolicy
from waku.messaging.handler import EventHandler
from waku.messaging.inbox._destination import handler_destination  # noqa: PLC2701
from waku.messaging.inbox.models import InboxEntry
from waku.messaging.router import route
from waku.messaging.transport.serialization import IEnvelopeSerializer
from waku.testing import create_test_app
from waku.uow import IUnitOfWork

from tests._wait import wait_until
from tests.messaging.helpers import FailingDeadLetterStore, FakeUoW, RecordingDeadLetterStore, make_envelope
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


# Inbox-only config (no outbox, no dead_letter_store): also exercises that IEnvelopeSerializer is
# registered for inbox-only setups (the durable endpoint serializes the envelope before persisting).
def _durable_config(inbox: FakeInboxStore) -> MessagingConfig:
    return MessagingConfig(
        endpoints=[local_queue('orders', mode=EndpointMode.DURABLE, stop_timeout=1.0, max_buffer_size=math.inf)],
        routing=[route(_OrderPlaced).to('orders')],
        inbox=InboxConfig(store=lambda: inbox, owner_id='test-node:1'),
        global_pipeline_behaviors=[TransactionalBehavior],
    )


class _FailingOrderHandler(EventHandler[_OrderPlaced]):
    @override
    async def handle(self, message: _OrderPlaced, /) -> None:
        msg = 'handler always fails'
        raise RuntimeError(msg)


# DLQ-failure config: the handler always fails (-> move_to_dead_letter), but the dead-letter store is
# unavailable (save raises). Exercises ERR-2 — a failed durable DLQ write must keep the inbox row.
def _dlq_failing_config(inbox: FakeInboxStore) -> MessagingConfig:
    return MessagingConfig(
        endpoints=[local_queue('orders', mode=EndpointMode.DURABLE, stop_timeout=1.0, max_buffer_size=math.inf)],
        routing=[route(_OrderPlaced).to('orders')],
        inbox=InboxConfig(store=lambda: inbox, owner_id='test-node:1'),
        default_error_policies=(ErrorPolicy.on_any_exception().move_to_dead_letter(),),
        dead_letter=DeadLetterConfig(store=FailingDeadLetterStore),
        global_pipeline_behaviors=[TransactionalBehavior],
    )


class TestDurableInboxIntegration:
    @staticmethod
    async def test_message_is_persisted_and_handled() -> None:
        _RecordingHandler.observed = []
        inbox = FakeInboxStore()
        async with (
            create_test_app(
                imports=[MessagingModule.register(_durable_config(inbox))],
                extensions=[MessagingExtension().bind(_RecordingHandler)],
                providers=[object_(FakeUoW(), provided_type=IUnitOfWork)],
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
                imports=[MessagingModule.register(_durable_config(inbox))],
                extensions=[MessagingExtension().bind(_RecordingHandler)],
                providers=[object_(FakeUoW(), provided_type=IUnitOfWork)],
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
                imports=[MessagingModule.register(_durable_config(inbox))],
                extensions=[
                    MessagingExtension().bind(_RecordingHandler, _SecondRecordingHandler),
                ],
                providers=[object_(FakeUoW(), provided_type=IUnitOfWork)],
            ) as app,
            app.container() as container,
        ):
            bus = await container.get(IMessageBus)
            await bus.publish(_OrderPlaced(order_id='fan-1'))
            await wait_until(lambda: handled_count() == 2)

            assert len(inbox.entries) == 2
            assert _RecordingHandler.observed == ['fan-1']
            assert _SecondRecordingHandler.observed == ['fan-1']

            purged = await inbox.cleanup_handled(datetime.now(tz=UTC) + timedelta(minutes=10))
            assert purged == 2

    @staticmethod
    async def test_failed_dead_letter_write_keeps_inbox_row_for_recovery() -> None:
        # ERR-2: when a durable message's DLQ write FAILS, its inbox row must survive (still INCOMING)
        # so the recovery drain re-runs it. Deleting it would lose the message from BOTH stores.
        inbox = FakeInboxStore()
        async with (
            create_test_app(
                imports=[MessagingModule.register(_dlq_failing_config(inbox))],
                extensions=[MessagingExtension().bind(_FailingOrderHandler)],
                providers=[object_(FakeUoW(), provided_type=IUnitOfWork)],
            ) as app,
            app.container() as container,
        ):
            bus = await container.get(IMessageBus)
            await bus.publish(_OrderPlaced(order_id='o-dlq-fail'))

        entries = list(inbox.entries.values())
        assert len(entries) == 1  # row KEPT, not deleted
        assert entries[0].status is InboxStatus.INCOMING  # recoverable; never marked HANDLED

    @staticmethod
    async def test_global_durable_default_makes_unset_local_queue_durable() -> None:
        _RecordingHandler.observed = []
        inbox = FakeInboxStore()
        config = MessagingConfig(
            endpoints=[local_queue('orders', stop_timeout=1.0)],  # mode unset -> inherits the global default
            routing=[route(_OrderPlaced).to('orders')],
            inbox=InboxConfig(store=lambda: inbox, owner_id='test-node:1'),
            global_pipeline_behaviors=[TransactionalBehavior],
            default_endpoint_mode=EndpointMode.DURABLE,
        )
        async with (
            create_test_app(
                imports=[MessagingModule.register(config)],
                extensions=[MessagingExtension().bind(_RecordingHandler)],
                providers=[object_(FakeUoW(), provided_type=IUnitOfWork)],
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
            endpoints=[local_queue('orders', mode=EndpointMode.BUFFERED, stop_timeout=1.0)],
            routing=[route(_OrderPlaced).to('orders')],
            inbox=InboxConfig(store=lambda: inbox, owner_id='test-node:1'),
            global_pipeline_behaviors=[TransactionalBehavior],
            default_endpoint_mode=EndpointMode.DURABLE,
        )
        async with (
            create_test_app(
                imports=[MessagingModule.register(config)],
                extensions=[MessagingExtension().bind(_RecordingHandler)],
                providers=[object_(FakeUoW(), provided_type=IUnitOfWork)],
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
            default_endpoint_mode=EndpointMode.DURABLE,
        )
        with pytest.raises(ImproperlyConfiguredError):
            MessagingModule.register(config)

    @staticmethod
    def test_inline_endpoint_with_requeue_policy_is_rejected() -> None:
        config = MessagingConfig(
            endpoints=[local_queue('orders', mode=EndpointMode.INLINE)],
            routing=[route(_OrderPlaced).to('orders')],
            default_error_policies=(ErrorPolicy.on_any_exception().requeue(),),
        )
        with pytest.raises(ImproperlyConfiguredError, match='INLINE'):
            MessagingModule.register(config)

    @staticmethod
    def test_inline_endpoint_with_pause_policy_is_rejected() -> None:
        config = MessagingConfig(
            endpoints=[local_queue('orders', mode=EndpointMode.INLINE)],
            routing=[route(_OrderPlaced).to('orders')],
            default_error_policies=(ErrorPolicy.on_any_exception().pause_processing(timedelta(minutes=5)),),
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
        # Recovery executor must honour default_execution_timeout like the live path. If it doesn't,
        # the drain blocks forever and the DLQ row never appears (wait_until trips).
        inbox = FakeInboxStore()
        dlq = RecordingDeadLetterStore()
        config = MessagingConfig(
            endpoints=[local_queue('orders', mode=EndpointMode.DURABLE, stop_timeout=1.0)],
            routing=[route(_OrderPlaced).to('orders')],
            inbox=InboxConfig(store=lambda: inbox, owner_id='node-a:1', recovery_interval=timedelta(seconds=0.01)),
            dead_letter=DeadLetterConfig(store=lambda: dlq),
            default_error_policies=(ErrorPolicy.on_any_exception().move_to_dead_letter(),),
            default_execution_timeout=timedelta(seconds=0.01),
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
            providers=[object_(FakeUoW(), provided_type=IUnitOfWork)],
        ) as app:
            serializer = await app.container.get(IEnvelopeSerializer)
            envelope = make_envelope(_OrderPlaced(order_id='rec-1'))
            entry = InboxEntry(
                id=envelope.message_id,
                payload=serializer.serialize(envelope),
                message_type=envelope.message_type,
                source_uri=EndpointUri('orders'),
                destination=handler_destination(_BlockingHandler),
                owner_id=None,
                status=InboxStatus.INCOMING,
                attempts=0,
            )
            inbox.entries[entry.id, entry.destination] = entry
            await wait_until(lambda: len(dlq.entries) == 1)

        assert len(dlq.entries) == 1
