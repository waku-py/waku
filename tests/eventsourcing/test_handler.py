from __future__ import annotations

from dataclasses import dataclass

from typing_extensions import override

from waku.cqrs import MediatorExtension, MediatorModule, Request
from waku.cqrs.contracts.notification import INotification
from waku.cqrs.interfaces import IMediator, IPublisher
from waku.eventsourcing.contracts.aggregate import EventSourcedAggregate
from waku.eventsourcing.contracts.stream import StreamId
from waku.eventsourcing.handler import EventSourcedCommandHandler
from waku.eventsourcing.modules import EventSourcingExtension, EventSourcingModule
from waku.eventsourcing.repository import EventSourcedRepository
from waku.modules import module
from waku.testing import create_test_app


@dataclass(frozen=True)
class NoteCreated(INotification):
    title: str


@dataclass(frozen=True)
class NoteEdited(INotification):
    content: str


class Note(EventSourcedAggregate):
    def __init__(self) -> None:
        super().__init__()
        self.title: str = ''
        self.content: str = ''

    def create(self, title: str) -> None:
        self._raise_event(NoteCreated(title=title))

    def edit(self, content: str) -> None:
        self._raise_event(NoteEdited(content=content))

    def _apply(self, event: INotification) -> None:
        match event:
            case NoteCreated(title=title):
                self.title = title
            case NoteEdited(content=content):
                self.content = content


class NoteRepository(EventSourcedRepository[Note]):
    aggregate_type_name = 'Note'

    @override
    def create_aggregate(self) -> Note:
        return Note()

    @override
    def _stream_id(self, aggregate_id: str) -> StreamId:
        return StreamId.for_aggregate('Note', aggregate_id)


@dataclass(frozen=True, kw_only=True)
class CreateNote(Request[None]):
    note_id: str
    title: str


class CreateNoteHandler(EventSourcedCommandHandler[CreateNote, None, Note]):
    def __init__(self, repository: NoteRepository, publisher: IPublisher) -> None:
        super().__init__(repository, publisher)

    @override
    def _aggregate_id(self, request: CreateNote) -> str:
        return request.note_id

    @override
    def _is_creation_command(self, request: CreateNote) -> bool:
        return True

    @override
    async def _execute(self, request: CreateNote, aggregate: Note) -> None:
        aggregate.create(request.title)


async def test_event_sourced_command_handler_creates_and_persists_aggregate() -> None:
    es_ext = EventSourcingExtension().bind_repository(NoteRepository)
    mediator_ext = MediatorExtension().bind_request(CreateNote, CreateNoteHandler)

    @module(
        imports=[EventSourcingModule.register(), MediatorModule.register()],
        extensions=[es_ext, mediator_ext],
    )
    class NoteModule:
        pass

    async with create_test_app(imports=[NoteModule]) as app, app.container() as container:
        mediator = await container.get(IMediator)
        await mediator.send(CreateNote(note_id='n-1', title='Hello'))

        repo = await container.get(NoteRepository)
        loaded = await repo.load('n-1')
        assert loaded.title == 'Hello'
        assert loaded.version == 0
