from __future__ import annotations

from typing import TYPE_CHECKING

from typing_extensions import override

from waku._internal.transaction import TransactionCleanupError
from waku.messaging._internal.transaction import CompletedExecutionError
from waku.messaging.errors._internal.replay import ReplayExecution

if TYPE_CHECKING:
    from waku.messaging.errors.dead_letter import DeadLetterEntry

__all__ = [
    'ReplayExecutor',
]


class ReplayExecutor(ReplayExecution):
    """Public dead-letter replay boundary that exposes the original rollback failure."""

    __slots__ = ()

    @override
    async def replay(self, entry: DeadLetterEntry) -> bool:
        try:
            return await super().replay(entry)
        except TransactionCleanupError as exc:
            raise exc.rollback_error from exc.primary_error
        except CompletedExecutionError as exc:
            raise exc.error from exc
