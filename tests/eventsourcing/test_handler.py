from __future__ import annotations

from dataclasses import dataclass

from typing_extensions import override

from waku.cqrs import MediatorExtension, MediatorModule, Request
from waku.cqrs.interfaces import IMediator
from waku.eventsourcing.handler import EventSourcedVoidCommandHandler
from waku.eventsourcing.modules import EventSourcingExtension, EventSourcingModule
from waku.modules import module
from waku.testing import create_test_app

from tests.eventsourcing.domain import Note, NoteCreated, NoteEdited, NoteRepository


@dataclass(frozen=True, kw_only=True)
class CreateNote(Request[None]):
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


async def test_event_sourced_command_handler_creates_and_persists_aggregate() -> None:
    @module(
        imports=[EventSourcingModule.register(), MediatorModule.register()],
        extensions=[
            EventSourcingExtension().bind_aggregate(repository=NoteRepository, event_types=[NoteCreated, NoteEdited]),
            MediatorExtension().bind_request(CreateNote, CreateNoteHandler),
        ],
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
