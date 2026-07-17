from __future__ import annotations

from dataclasses import dataclass

from typing_extensions import override

from waku.integrations.eventsourcing_messaging import EventSourcedVoidCommandHandler
from waku.messaging import IRequest

from tests.eventsourcing.domain import Note


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
