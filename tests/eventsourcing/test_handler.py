from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pytest
from typing_extensions import override

from waku.eventsourcing.contracts.stream import StreamId
from waku.eventsourcing.exceptions import ConcurrencyConflictError, EventSourcingError
from waku.eventsourcing.modules import EventSourcingConfig, EventSourcingExtension, EventSourcingModule
from waku.eventsourcing.serialization.registry import EventTypeRegistry
from waku.eventsourcing.store.in_memory import InMemoryEventStore
from waku.integrations.eventsourcing_messaging import EventSourcedVoidCommandHandler
from waku.messaging import IRequest, MessagingExtension, MessagingModule
from waku.messaging.interfaces import IMessageBus
from waku.modules import module
from waku.testing import create_test_app

from tests.eventsourcing.domain import Note, NoteCreated, NoteEdited, NoteRepository
from tests.eventsourcing.helpers import RecordingContext, fail_save_n_times

if TYPE_CHECKING:
    from contextlib import AbstractAsyncContextManager

    from pytest_mock import MockerFixture


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


@dataclass(frozen=True, kw_only=True)
class CreateNoteWithKey(IRequest):
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
    def _idempotency_key(self, request: CreateNoteWithKey, version: int) -> str | None:
        return request.idempotency_key


@dataclass(frozen=True, kw_only=True)
class EditNote(IRequest):
    note_id: str
    content: str


class EditNoteHandler(EventSourcedVoidCommandHandler[EditNote, Note]):
    @override
    def _aggregate_id(self, request: EditNote) -> str:
        return request.note_id

    @override
    async def _execute(self, request: EditNote, aggregate: Note) -> None:
        aggregate.edit(request.content)


class NoRetryEditNoteHandler(EditNoteHandler):
    max_attempts = 1


class TwoAttemptEditNoteHandler(EditNoteHandler):
    max_attempts = 2


def _make_handler_deps() -> tuple[NoteRepository, InMemoryEventStore]:
    registry = EventTypeRegistry()
    registry.register(NoteCreated)
    registry.register(NoteEdited)
    event_store = InMemoryEventStore(registry=registry)
    return NoteRepository(event_store=event_store), event_store


async def _create_note(repo: NoteRepository, note_id: str = 'n-1') -> None:
    await CreateNoteHandler(repository=repo).handle(CreateNote(note_id=note_id, title='Hello'))


async def test_retry_succeeds_on_second_attempt(mocker: MockerFixture) -> None:
    repo, _ = _make_handler_deps()
    await _create_note(repo)

    handler = EditNoteHandler(repository=repo)
    conflict = ConcurrencyConflictError(
        stream_id=StreamId.for_aggregate('Note', 'n-1'), expected_version=0, actual_version=1
    )
    mock_save = mocker.patch.object(repo, 'save', side_effect=fail_save_n_times(repo.save, conflict))

    await handler.handle(EditNote(note_id='n-1', content='Updated'))

    assert mock_save.call_count == 2
    assert (await repo.load('n-1')).content == 'Updated'


async def test_retry_exhausted_raises_concurrency_error(mocker: MockerFixture) -> None:
    repo, _ = _make_handler_deps()
    await _create_note(repo)

    handler = TwoAttemptEditNoteHandler(repository=repo)
    conflict = ConcurrencyConflictError(
        stream_id=StreamId.for_aggregate('Note', 'n-1'), expected_version=0, actual_version=1
    )
    mock_save = mocker.patch.object(repo, 'save', side_effect=conflict)

    with pytest.raises(ConcurrencyConflictError):
        await handler.handle(EditNote(note_id='n-1', content='Updated'))

    assert mock_save.call_count == 2


async def test_creation_command_creates_aggregate_via_create_aggregate() -> None:
    repo, _ = _make_handler_deps()
    handler = CreateNoteHandler(repository=repo)

    await handler.handle(CreateNote(note_id='n-new', title='Brand New'))

    loaded = await repo.load('n-new')
    assert loaded.title == 'Brand New'
    assert loaded.version == 0


async def test_creation_command_not_retried(mocker: MockerFixture) -> None:
    repo, _ = _make_handler_deps()
    handler = CreateNoteHandler(repository=repo)
    conflict = ConcurrencyConflictError(
        stream_id=StreamId.for_aggregate('Note', 'n-1'), expected_version=-1, actual_version=0
    )
    mock_save = mocker.patch.object(repo, 'save', side_effect=conflict)

    with pytest.raises(ConcurrencyConflictError):
        await handler.handle(CreateNote(note_id='n-1', title='Hello'))

    mock_save.assert_awaited_once()


async def test_max_attempts_1_no_retry(mocker: MockerFixture) -> None:
    repo, _ = _make_handler_deps()
    await _create_note(repo)

    handler = NoRetryEditNoteHandler(repository=repo)
    conflict = ConcurrencyConflictError(
        stream_id=StreamId.for_aggregate('Note', 'n-1'), expected_version=0, actual_version=1
    )
    mock_save = mocker.patch.object(repo, 'save', side_effect=conflict)

    with pytest.raises(ConcurrencyConflictError):
        await handler.handle(EditNote(note_id='n-1', content='Updated'))

    assert mock_save.call_count == 1


async def test_non_concurrency_error_not_retried(mocker: MockerFixture) -> None:
    repo, _ = _make_handler_deps()
    await _create_note(repo)

    handler = EditNoteHandler(repository=repo)
    mock_save = mocker.patch.object(repo, 'save', side_effect=EventSourcingError('generic error'))

    with pytest.raises(EventSourcingError, match='generic error'):
        await handler.handle(EditNote(note_id='n-1', content='Updated'))

    assert mock_save.call_count == 1


async def test_event_sourced_command_handler_creates_and_persists_aggregate() -> None:
    @module(
        imports=[
            EventSourcingModule.register(EventSourcingConfig(store=InMemoryEventStore)),
            MessagingModule.register(),
        ],
        extensions=[
            EventSourcingExtension().bind_aggregate(repository=NoteRepository, event_types=[NoteCreated, NoteEdited]),
            MessagingExtension().bind(CreateNoteHandler),
        ],
    )
    class NoteModule:
        pass

    async with create_test_app(imports=[NoteModule]) as app, app.container() as container:
        bus = await container.get(IMessageBus)
        await bus.invoke(CreateNote(note_id='n-1', title='Hello'))

        repo = await container.get(NoteRepository)
        loaded = await repo.load('n-1')
        assert loaded.title == 'Hello'
        assert loaded.version == 0


async def test_default_idempotency_key_passes_none_to_repository(mocker: MockerFixture) -> None:
    repo, _ = _make_handler_deps()
    handler = CreateNoteHandler(repository=repo)

    save_spy = mocker.spy(repo, 'save')
    await handler.handle(CreateNote(note_id='n-1', title='Hello'))

    save_spy.assert_awaited_once()
    _, kwargs = save_spy.call_args
    assert kwargs['idempotency_key'] is None


async def test_idempotency_key_passed_to_repository_save(mocker: MockerFixture) -> None:
    repo, _ = _make_handler_deps()
    handler = CreateNoteWithIdempotencyKeyHandler(repository=repo)

    save_spy = mocker.spy(repo, 'save')
    await handler.handle(CreateNoteWithKey(note_id='n-1', title='Hello', idempotency_key='key-123'))

    save_spy.assert_awaited_once()
    _, kwargs = save_spy.call_args
    assert kwargs['idempotency_key'] == 'key-123'


def test_max_attempts_zero_raises_value_error() -> None:
    with pytest.raises(ValueError, match='max_attempts must be >= 1'):
        # noinspection PyUnusedLocal
        class ZeroAttemptHandler(EditNoteHandler):
            max_attempts = 0


class EditNoteWithContextHandler(EventSourcedVoidCommandHandler[EditNote, Note]):
    def __init__(
        self,
        repository: NoteRepository,
        context: RecordingContext,
    ) -> None:
        super().__init__(repository)
        self._context = context

    @override
    def _aggregate_id(self, request: EditNote) -> str:
        return request.note_id

    @override
    async def _execute(self, request: EditNote, aggregate: Note) -> None:
        aggregate.edit(request.content)

    @override
    def _create_attempt_context(self) -> AbstractAsyncContextManager[Any]:
        return self._context


async def test_attempt_context_entered_per_attempt() -> None:
    repo, _ = _make_handler_deps()
    await _create_note(repo)
    ctx = RecordingContext()

    handler = EditNoteWithContextHandler(repository=repo, context=ctx)
    await handler.handle(EditNote(note_id='n-1', content='Updated'))

    assert ctx.entered == 1
    assert ctx.exited == 1


async def test_attempt_context_entered_per_retry_attempt(mocker: MockerFixture) -> None:
    repo, _ = _make_handler_deps()
    await _create_note(repo)

    contexts: list[RecordingContext] = []

    class RetryEditWithContextHandler(EventSourcedVoidCommandHandler[EditNote, Note]):
        max_attempts = 3

        @override
        def _aggregate_id(self, request: EditNote) -> str:
            return request.note_id

        @override
        async def _execute(self, request: EditNote, aggregate: Note) -> None:
            aggregate.edit(request.content)

        @override
        def _create_attempt_context(self) -> AbstractAsyncContextManager[Any]:
            c = RecordingContext()
            contexts.append(c)
            return c

    handler = RetryEditWithContextHandler(repository=repo)
    conflict = ConcurrencyConflictError(
        stream_id=StreamId.for_aggregate('Note', 'n-1'), expected_version=0, actual_version=1
    )
    mocker.patch.object(repo, 'save', side_effect=fail_save_n_times(repo.save, conflict))

    await handler.handle(EditNote(note_id='n-1', content='Updated'))

    assert len(contexts) == 2
    assert all(c.entered == 1 and c.exited == 1 for c in contexts)


async def test_idempotency_key_includes_current_version_in_stored_events() -> None:
    repo, event_store = _make_handler_deps()
    await _create_note(repo)
    note = await repo.load('n-1')
    note.edit('extra edit')
    await repo.save('n-1', note)

    class VersionAwareHandler(EventSourcedVoidCommandHandler[EditNote, Note]):
        @override
        def _aggregate_id(self, request: EditNote) -> str:
            return request.note_id

        @override
        async def _execute(self, request: EditNote, aggregate: Note) -> None:
            aggregate.edit(request.content)

        @override
        def _idempotency_key(self, request: EditNote, version: int) -> str | None:
            return f'{request.note_id}:edit:{version}'

    handler = VersionAwareHandler(repository=repo)

    await handler.handle(EditNote(note_id='n-1', content='Updated'))

    stream_id = StreamId.for_aggregate('Note', 'n-1')
    stored = await event_store.read_stream(stream_id)
    last_event = stored[-1]
    assert last_event.idempotency_key == 'n-1:edit:1:0'
