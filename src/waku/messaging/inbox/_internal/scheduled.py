from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from typing_extensions import override

from waku._internal.clock import utc_now
from waku._internal.transaction import unit_of_work_scope
from waku.di import is_registered
from waku.exceptions import ImproperlyConfiguredError
from waku.messaging._internal.polling_agent import FixedPace, Placement, PollingAgent
from waku.messaging.durability import IInboxStore
from waku.messaging.partition import ISequenceAllocator

if TYPE_CHECKING:
    from dishka import AsyncContainer

    from waku._internal.clock import Now
    from waku.messaging._internal.identifiers import GroupId
    from waku.messaging.inbox.config import InboxConfig

__all__ = [
    'ScheduledPromotionWorker',
]

logger = logging.getLogger(__name__)

# Per-node jitter: multi-pod FOR UPDATE SKIP LOCKED claims don't stomp (Wolverine parity).
_PROMOTION_JITTER_FACTOR = 0.1


class _AbsentSequenceAllocator(ISequenceAllocator):
    """Stand-in when no ISequenceAllocator is registered (durable endpoint without partition_by).

    Keyless scheduled rows never call ``allocate`` (group_id is None), so this lets them promote.
    A KEYED scheduled row reaching here is a misconfiguration that static validation can't catch
    (an explicit ``DeliveryOptions.group_id`` with no allocator) — fail loud with a domain error.
    """

    @override
    async def allocate(self, group_id: GroupId) -> int:  # pragma: no cover - defensive; keyless never calls
        msg = (
            f'Scheduled message has group_id {group_id!r} but no ISequenceAllocator is registered; '
            'register one or omit partition keys from scheduled messages.'
        )
        raise ImproperlyConfiguredError(msg)


_ABSENT_ALLOCATOR = _AbsentSequenceAllocator()


class ScheduledPromotionWorker(PollingAgent):
    """Promotes due SCHEDULED inbox rows to INCOMING on a jittered cadence.

    Sibling of ``InboxRecoveryWorker``, started/stopped together so promotion travels with recovery
    at M4+ leader election. Runs its own ``scheduled_poll_interval`` (Wolverine ``ScheduledJobPollingTime``
    parity) — NOT the slower recovery tick. PER_POD: ``FOR UPDATE SKIP LOCKED`` handles concurrency.
    """

    placement = Placement.PER_POD

    __slots__ = ('_config', '_container', '_now')

    def __init__(self, *, container: AsyncContainer, config: InboxConfig, now: Now = utc_now) -> None:
        self._container = container
        self._config = config
        self._now = now
        super().__init__(stop_timeout=config.stop_timeout)

    @override
    def _make_pace(self) -> FixedPace:
        return FixedPace(self._config.scheduled_poll_interval.total_seconds(), jitter_factor=_PROMOTION_JITTER_FACTOR)

    @override
    async def _tick(self) -> int:
        async with unit_of_work_scope(self._container) as scope:
            store = await scope.get(IInboxStore)
            # No allocator is registered without partition_by; keyless rows promote without one.
            if await is_registered(scope, ISequenceAllocator):
                allocator: ISequenceAllocator = await scope.get(ISequenceAllocator)
            else:
                allocator = _ABSENT_ALLOCATOR
            promoted: int = await store.promote_due_scheduled(self._now(), allocator, self._config.batch_size)
        if promoted > 0:
            logger.info('Promoted %d due scheduled inbox entries to INCOMING', promoted)
        return promoted
