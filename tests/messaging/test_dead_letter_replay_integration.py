from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from typing_extensions import override

from waku.di import object_
from waku.messaging import (
    IMessageBus,
    IRequest,
    MessagingConfig,
    MessagingExtension,
    MessagingModule,
    RequestHandler,
)
from waku.messaging.config import DeadLetterConfig
from waku.messaging.errors.dead_letter import DeadLetterEntry, DeadLetterQuery, DeadLetterStatus, IDeadLetterStore
from waku.messaging.errors.policy import ErrorPolicy
from waku.messaging.errors.replay import ReplayExecutor
from waku.testing import create_test_app
from waku.uow import IUnitOfWork

from tests.messaging.helpers import FakeUoW, wait_until

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime
    from uuid import UUID


@dataclass(frozen=True, slots=True)
class _Charge(IRequest[None]):
    amount: int


_attempts: list[int] = []


class _ChargeHandler(RequestHandler[_Charge, None]):
    @override
    async def handle(self, request: _Charge, /) -> None:
        _attempts.append(request.amount)
        if len(_attempts) == 1:
            msg = 'first attempt fails'
            raise RuntimeError(msg)


class _DictDeadLetterStore(IDeadLetterStore):
    def __init__(self) -> None:
        self.rows: dict[UUID, DeadLetterEntry] = {}

    @override
    async def save(self, entry: DeadLetterEntry) -> None:
        self.rows[entry.id] = entry

    @override
    async def fetch_one(self, entry_id: UUID) -> DeadLetterEntry:
        return self.rows[entry_id]

    @override
    async def mark_replayed(self, entry_id: UUID) -> None:
        self.rows[entry_id] = replace(self.rows[entry_id], status=DeadLetterStatus.REPLAYED)

    @override
    async def mark_replay_failed(self, entry_id: UUID, error: str) -> None:  # pragma: no cover
        self.rows[entry_id] = replace(self.rows[entry_id], status=DeadLetterStatus.REPLAY_FAILED)

    @override
    async def fetch(self, batch_size: int = 100) -> Sequence[DeadLetterEntry]:  # pragma: no cover
        return list(self.rows.values())

    @override
    async def claim_replayable(
        self, batch_size: int, max_replay_count: int
    ) -> Sequence[DeadLetterEntry]:  # pragma: no cover
        return []

    @override
    async def query(self, filters: DeadLetterQuery) -> Sequence[DeadLetterEntry]:  # pragma: no cover
        return list(self.rows.values())

    @override
    async def delete(self, entry_id: UUID) -> None:  # pragma: no cover
        self.rows.pop(entry_id, None)

    @override
    async def purge(self, older_than: datetime) -> int:  # pragma: no cover
        return 0


async def test_dead_letter_then_replay_reprocesses_message() -> None:
    _attempts.clear()
    dl_store = _DictDeadLetterStore()
    config = MessagingConfig(
        default_error_policies=(ErrorPolicy.on_any_exception().move_to_dead_letter(),),
        dead_letter=DeadLetterConfig(store=lambda: dl_store),
    )

    async with (
        create_test_app(
            imports=[MessagingModule.register(config)],
            extensions=[MessagingExtension().bind(_Charge, _ChargeHandler)],
            providers=[object_(FakeUoW(), provided_type=IUnitOfWork)],
        ) as app,
        app.container() as scope,
    ):
        bus = await scope.get(IMessageBus)
        await bus.send(_Charge(amount=42))
        await wait_until(lambda: bool(dl_store.rows))

        entry_id = next(iter(dl_store.rows))
        replayer = await scope.get(ReplayExecutor)
        assert await replayer.replay_by_id(entry_id) is True
        await wait_until(lambda: len(_attempts) == 2)

    assert _attempts == [42, 42]
    assert dl_store.rows[entry_id].status is DeadLetterStatus.REPLAYED
