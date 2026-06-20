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
            # endpoint.stop() (app shutdown) drains the worker deterministically.

        entries = list(inbox.entries.values())
        assert len(entries) == 1
        assert entries[0].status is InboxStatus.HANDLED
        assert _RecordingHandler.observed == ['o-1']

    @staticmethod
    async def test_distinct_publishes_each_persist_and_handle() -> None:
        # Two publishes => two distinct envelopes (distinct message_ids) => two inbox rows,
        # handler invoked twice. The same-message_id dedup path is proven in test_receiver.py.
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
        # One message routed to a durable endpoint with two subscribed handlers: persist-before-enqueue
        # writes two `(id, destination)` rows, both handlers run, both rows end HANDLED. After the
        # retention window purges the rows, a fresh delivery would re-run both (windowed dedup).
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

            # Retention window purges the HANDLED rows -> dedup window closes.
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
            # app shutdown drains the worker deterministically (see test_message_is_persisted_and_handled).

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
            # app shutdown drains the durable worker deterministically.

        # The unset endpoint went durable: a persisted-then-handled inbox row proves it (a BUFFERED
        # endpoint would never touch the inbox).
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

        # The explicit per-endpoint mode wins over the durable global default: the handler ran via the
        # buffered worker and the (wired) inbox was never written.
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
    async def test_recovery_drain_applies_execution_timeout_to_durable_handler() -> None:
        # An abandoned inbox row recovered by the background drainer must be subject to
        # default_execution_timeout exactly like the live path: a blocking handler is timed out and
        # dead-lettered. If build_inbox_drainer fails to thread the config timeout into its executor,
        # the recovery executor has no deadline, the drain blocks forever, and the DLQ row never appears
        # (wait_until trips). The DLQ entry is the unambiguous "the timeout fired" signal.
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
