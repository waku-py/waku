from __future__ import annotations

from dataclasses import dataclass

from typing_extensions import override

from waku.eventsourcing.contracts.stream import StreamId
from waku.eventsourcing.modules import EventSourcingConfig, EventSourcingExtension, EventSourcingModule
from waku.eventsourcing.store.in_memory import InMemoryEventStore
from waku.eventsourcing.store.interfaces import IEventStore
from waku.integrations.eventsourcing_messaging import EventSourcedVoidCommandHandler
from waku.messaging import IRequest, MessagingExtension, MessagingModule
from waku.messaging.enrichers import CorrelationEnricher
from waku.messaging.interfaces import IMessageBus
from waku.modules import module
from waku.testing import create_test_app

from tests.eventsourcing.domain import Note, NoteCreated, NoteRepository


@dataclass(frozen=True, kw_only=True)
class CreateNote(IRequest):
    note_id: str
    title: str


class CreateNoteHandler(EventSourcedVoidCommandHandler[CreateNote, Note]):
    @override
    def _aggregate_id(self, request: CreateNote) -> str:
        return request.note_id

    @override
    def _is_creation_command(self, request: CreateNote) -> bool:
        return True

    @override
    async def _execute(self, request: CreateNote, aggregate: Note) -> None:
        aggregate.create(request.title)


class TestESBusIntegration:
    @staticmethod
    async def test_correlation_metadata_propagated_to_event_store() -> None:
        es_ext = EventSourcingExtension()
        es_ext.bind_aggregate(
            repository=NoteRepository,
            event_types=[NoteCreated],
        )

        msg_ext = MessagingExtension().bind(CreateNoteHandler)

        @module(extensions=[es_ext, msg_ext])
        class TestModule:
            pass

        async with (
            create_test_app(
                imports=[
                    MessagingModule.register(),
                    EventSourcingModule.register(
                        EventSourcingConfig(
                            store=InMemoryEventStore,
                            enrichers=[CorrelationEnricher],
                        ),
                    ),
                    TestModule,
                ],
            ) as app,
            app.container() as container,
        ):
            bus = await container.get(IMessageBus)
            await bus.invoke(CreateNote(note_id='n-1', title='Test Note'))

            event_store = await container.get(IEventStore)
            events = await event_store.read_stream(StreamId.for_aggregate('Note', 'n-1'))

        assert len(events) == 1
        assert events[0].metadata.correlation_id is not None
        assert events[0].metadata.causation_id is not None
