from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from typing_extensions import override

from waku.cqrs import MediatorExtension, MediatorModule, Request
from waku.cqrs.interfaces import IMediator, IPublisher
from waku.eventsourcing.handler import EventSourcedVoidCommandHandler
from waku.eventsourcing.modules import EventSourcingExtension, EventSourcingModule
from waku.eventsourcing.serialization.registry import EventTypeRegistry
from waku.eventsourcing.store.in_memory import InMemoryEventStore
from waku.modules import module
from waku.testing import create_test_app

from tests.eventsourcing.domain import Note, NoteCreated, NoteEdited, NoteRepository

if TYPE_CHECKING:
    from pytest_mock import MockerFixture


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


@dataclass(frozen=True, kw_only=True)
class CreateNoteWithKey(Request[None]):
    note_id: str
    title: str
    idempotency_key: str


class CreateNoteWithIdempotencyKeyHandler(EventSourcedVoidCommandHandler[CreateNoteWithKey, Note]):
    @override
    def _aggregate_id(self, request: CreateNoteWithKey) -> str:
        return request.note_id

    @override
    def _is_creation_command(self, request: CreateNoteWithKey) -> bool:
        return True

    @override
    async def _execute(self, request: CreateNoteWithKey, aggregate: Note) -> None:
        aggregate.create(request.title)

    @override
    def _idempotency_key(self, request: CreateNoteWithKey) -> str | None:
        return request.idempotency_key


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


async def test_default_idempotency_key_passes_none_to_repository(mocker: MockerFixture) -> None:
    registry = EventTypeRegistry()
    registry.register(NoteCreated)
    registry.register(NoteEdited)
    event_store = InMemoryEventStore(registry=registry)
    repo = NoteRepository(event_store=event_store)
    publisher = mocker.AsyncMock(spec=IPublisher)
    handler = CreateNoteHandler(repository=repo, publisher=publisher)

    save_spy = mocker.spy(repo, 'save')
    await handler.handle(CreateNote(note_id='n-1', title='Hello'))

    save_spy.assert_awaited_once()
    _, kwargs = save_spy.call_args
    assert kwargs['idempotency_key'] is None


async def test_idempotency_key_passed_to_repository_save(mocker: MockerFixture) -> None:
    registry = EventTypeRegistry()
    registry.register(NoteCreated)
    registry.register(NoteEdited)
    event_store = InMemoryEventStore(registry=registry)
    repo = NoteRepository(event_store=event_store)
    publisher = mocker.AsyncMock(spec=IPublisher)
    handler = CreateNoteWithIdempotencyKeyHandler(repository=repo, publisher=publisher)

    save_spy = mocker.spy(repo, 'save')
    await handler.handle(CreateNoteWithKey(note_id='n-1', title='Hello', idempotency_key='key-123'))

    save_spy.assert_awaited_once()
    _, kwargs = save_spy.call_args
    assert kwargs['idempotency_key'] == 'key-123'
