from __future__ import annotations

from typing import TYPE_CHECKING

from waku import module
from waku.eventsourcing.contracts.event import EventEnvelope
from waku.eventsourcing.contracts.stream import NoStream, StreamId
from waku.eventsourcing.modules import EventSourcingConfig, EventSourcingExtension, EventSourcingModule
from waku.eventsourcing.store.in_memory import InMemoryEventStore
from waku.eventsourcing.store.interfaces import IEventStore
from waku.integrations.eventsourcing_messaging import EventSourcingMessagingModule
from waku.messages import IMessage
from waku.messaging.context import message_context_scope
from waku.testing import create_test_app

from tests.eventsourcing.domain import NoteCreated, NoteRepository
from tests.messaging.helpers import make_envelope

if TYPE_CHECKING:
    from typing import Any

    from waku import DynamicModule
    from waku.eventsourcing.contracts.event import EventMetadata
    from waku.messaging.contracts.envelope import MessageEnvelope


class _Probe(IMessage):
    pass


def _note_binding_module() -> type:
    es_ext = EventSourcingExtension()
    es_ext.bind_aggregate(repository=NoteRepository, event_types=[NoteCreated])

    @module(extensions=[es_ext])
    class NoteBindingModule:
        pass

    return NoteBindingModule


async def _append_within_context(bridge: DynamicModule) -> tuple[EventMetadata, MessageEnvelope[Any]]:
    envelope = make_envelope(_Probe())
    stream_id = StreamId.for_aggregate('Note', 'n-1')
    async with (
        create_test_app(
            imports=[
                EventSourcingModule.register(EventSourcingConfig(store=InMemoryEventStore)),
                bridge,
                _note_binding_module(),
            ],
        ) as app,
        app.container() as scope,
    ):
        store = await scope.get(IEventStore)
        with message_context_scope(envelope):
            await store.append_to_stream(
                stream_id,
                [EventEnvelope(domain_event=NoteCreated(title='t'), idempotency_key='k-1')],
                expected_version=NoStream(),
            )
        events = await store.read_stream(stream_id)

    assert len(events) == 1
    return events[0].metadata, envelope


async def test_bridge_autowires_correlation_enricher() -> None:
    metadata, envelope = await _append_within_context(EventSourcingMessagingModule.register())

    assert metadata.correlation_id == envelope.correlation_id
    assert metadata.causation_id == str(envelope.message_id)


async def test_opt_out_disables_enrichment() -> None:
    metadata, _ = await _append_within_context(
        EventSourcingMessagingModule.register(enrich_correlation=False),
    )

    assert metadata.correlation_id is None
    assert metadata.causation_id is None
