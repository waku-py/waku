from __future__ import annotations

from waku import module
from waku.backends.memory import MemoryBackend
from waku.eventsourcing.contracts.stream import StreamId
from waku.eventsourcing.modules import EventSourcingConfig, EventSourcingExtension, EventSourcingModule
from waku.eventsourcing.store.interfaces import IEventStore
from waku.integrations.eventsourcing_messaging import CorrelationEnricher
from waku.messaging import MessagingExtension, MessagingModule
from waku.messaging.interfaces import IMessageBus
from waku.testing import create_test_app

from tests.eventsourcing.domain import NoteCreated, NoteRepository
from tests.integrations.eventsourcing_messaging.domain import CreateNote, CreateNoteHandler


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
                            enrichers=[CorrelationEnricher],
                        ),
                    ),
                    MemoryBackend.register(),
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
