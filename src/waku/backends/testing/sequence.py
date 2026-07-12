from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

import pytest

from waku.messaging.config import MessagingConfig
from waku.messaging.modules import MessagingModule
from waku.messaging.partition import ISequenceAllocator
from waku.testing import create_test_app
from waku.uow import IUnitOfWork

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from waku.application import WakuApplication
    from waku.modules._internal.metadata import DynamicModule

__all__ = ['SequenceAllocatorContract']


async def _allocate(allocator: ISequenceAllocator, group_id: str) -> int:
    # GroupId is a messaging-internal NewType over str; the kit stays outside that privacy
    # boundary, so the port call is funneled through this seam instead of importing the NewType.
    return await allocator.allocate(group_id)  # type: ignore[arg-type]  # pyrefly: ignore[bad-argument-type]


class SequenceAllocatorContract:
    """Backend-owned sequencing contract: per-group monotonic allocation on the backend's resource.

    Subclass in your backend's test suite and override the ``backend_module`` fixture to return
    your registered backend (plus any resource setup/teardown around the yield). Backends whose
    ``IUnitOfWork`` cannot stage-and-roll-back real writes (e.g. an in-memory wiring stub) opt out
    of the rollback-coupling assertion with ``supports_rollback = False``.
    """

    supports_rollback: ClassVar[bool] = True

    @pytest.fixture
    def backend_module(self) -> DynamicModule:
        msg = 'override the backend_module fixture with your registered backend'
        raise NotImplementedError(msg)  # pragma: no cover

    @pytest.fixture
    async def app(self, backend_module: DynamicModule) -> AsyncIterator[WakuApplication]:
        # A bare MessagingConfig activates the backend's messaging wiring (the allocator provider
        # is unconditional) without starting durable workers whose polling could interleave with
        # the allocation assertions.
        async with create_test_app(
            imports=[
                MessagingModule.register(MessagingConfig()),
                backend_module,
            ],
        ) as app:
            yield app

    async def test_allocation_starts_at_one_and_is_monotonic_per_group(self, app: WakuApplication) -> None:
        async with app.container() as scope:
            allocator = await scope.get(ISequenceAllocator)

            first = await _allocate(allocator, 'contract-group-a')
            second = await _allocate(allocator, 'contract-group-a')
            third = await _allocate(allocator, 'contract-group-a')

            assert (first, second, third) == (1, 2, 3)

    async def test_distinct_groups_allocate_independently(self, app: WakuApplication) -> None:
        async with app.container() as scope:
            allocator = await scope.get(ISequenceAllocator)

            await _allocate(allocator, 'contract-group-a')
            await _allocate(allocator, 'contract-group-a')

            assert await _allocate(allocator, 'contract-group-b') == 1
            assert await _allocate(allocator, 'contract-group-a') == 3

    async def test_rolled_back_allocation_is_discarded(self, app: WakuApplication) -> None:
        if not self.supports_rollback:
            pytest.skip('backend opts out: its IUnitOfWork does not stage-and-roll-back real writes')

        async with app.container() as scope:
            allocator = await scope.get(ISequenceAllocator)
            uow = await scope.get(IUnitOfWork)

            assert await _allocate(allocator, 'contract-rollback') == 1
            await uow.rollback()

        async with app.container() as scope:
            allocator = await scope.get(ISequenceAllocator)

            # Co-commit proof: the rolled-back allocation left no trace, the number repeats.
            assert await _allocate(allocator, 'contract-rollback') == 1
