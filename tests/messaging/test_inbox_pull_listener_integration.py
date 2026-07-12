from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import ClassVar

from typing_extensions import override

from waku.di import object_
from waku.messages import IEvent
from waku.messaging import (
    EndpointMode,
    EventHandler,
    MessagingConfig,
    MessagingExtension,
    MessagingModule,
    TransactionalBehavior,
    local_queue,
    route,
)
from waku.messaging._internal.identifiers import EndpointUri
from waku.messaging.durability import IInboxStore
from waku.messaging.inbox.config import InboxConfig
from waku.messaging.inbox.destination import handler_destination
from waku.messaging.inbox.models import InboxEntry, InboxStatus
from waku.messaging.partition import ISequenceAllocator
from waku.messaging.transport._internal.wire import encode_metadata, encode_payload
from waku.serialization.codec import PayloadCodec
from waku.testing import create_test_app
from waku.uow import IUnitOfWork

from tests._wait import wait_until
from tests.messaging.helpers import FakeUoW, RecordingAllocator, make_envelope
from tests.messaging.inbox.fake_store import FakeInboxStore


@dataclass(frozen=True, kw_only=True)
class _OrderPlaced(IEvent):
    order_id: str


class _RecordingHandler(EventHandler[_OrderPlaced]):
    invocations: ClassVar[list[str]] = []

    @override
    async def handle(self, message: _OrderPlaced, /) -> None:
        self.invocations.append(message.order_id)


async def test_abandoned_row_is_drained_and_handled() -> None:
    _RecordingHandler.invocations = []
    inbox = FakeInboxStore()
    config = MessagingConfig(
        endpoints=[local_queue('local://orders', mode=EndpointMode.DURABLE)],
        routing=[route(_OrderPlaced).to('local://orders')],
        inbox=InboxConfig(recovery_interval=timedelta(seconds=0.01)),
        global_pipeline_behaviors=[TransactionalBehavior],
    )
    async with (
        create_test_app(
            imports=[MessagingModule.register(config)],
            extensions=[MessagingExtension().bind(_RecordingHandler)],
            providers=[
                object_(FakeUoW(), provided_type=IUnitOfWork),
                object_(inbox, provided_type=IInboxStore),
                object_(RecordingAllocator(), provided_type=ISequenceAllocator),
            ],
        ) as app,
        app.container() as scope,
    ):
        codec = await scope.get(PayloadCodec)
        envelope = make_envelope(_OrderPlaced(order_id='abandoned-1'))
        destination = handler_destination(_RecordingHandler)
        # Stage an abandoned INCOMING row (owner NULL) as if a prior node crashed before processing it.
        # Uses the decomposed row shape: encoded payload + metadata_ + typed correlation/causation columns.
        inbox.entries[envelope.message_id, destination] = InboxEntry(
            id=envelope.message_id,
            payload=encode_payload(envelope, codec),
            message_type=envelope.message_type,
            source_uri=EndpointUri('local://orders'),
            destination=destination,
            owner_id=None,
            status=InboxStatus.INCOMING,
            correlation_id=envelope.correlation_id,
            causation_id=envelope.causation_id,
            metadata_=encode_metadata(envelope),
        )
        # The app's InboxRecoveryWorker (its lifecycle-built drainer) claims + processes it within a few ticks.
        await wait_until(lambda: _RecordingHandler.invocations == ['abandoned-1'])
        await wait_until(lambda: inbox.entries[envelope.message_id, destination].status is InboxStatus.HANDLED)
