from __future__ import annotations

import logging
from typing import TYPE_CHECKING, TypeVar

from waku.eventsourcing.exceptions import ConcurrencyConflictError

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)

_T = TypeVar('_T')


async def execute_with_optimistic_retry(
    attempt_fn: Callable[[], Awaitable[_T]],
    *,
    max_attempts: int,
    is_creation: bool,
    request_name: str,
    aggregate_id: str,
) -> _T:
    for attempt in range(1, max_attempts + 1):
        if attempt > 1:
            logger.info(
                'Retrying %s for %s (attempt %d/%d) after concurrency conflict',
                request_name,
                aggregate_id,
                attempt,
                max_attempts,
            )

        try:
            return await attempt_fn()
        except ConcurrencyConflictError:
            if is_creation or attempt == max_attempts:
                raise

    msg = 'max_attempts must be >= 1'  # pragma: no cover
    raise ValueError(msg)  # pragma: no cover
