from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from typing_extensions import override

from waku.messages import IEvent
from waku.messaging._internal.identifiers import GroupId
from waku.messaging._internal.partition import resolve_and_allocate, resolve_group_id
from waku.messaging.partition import ISequenceAllocator

from tests.messaging.helpers import make_envelope

if TYPE_CHECKING:
    from dishka import AsyncContainer


@dataclass(frozen=True, slots=True)
class _Evt(IEvent):
    value: str = 'v'


class _FakeAllocator(ISequenceAllocator):
    def __init__(self) -> None:
        self.allocated: list[GroupId] = []

    @override
    async def allocate(self, group_id: GroupId) -> int:
        self.allocated.append(group_id)
        return len(self.allocated)


class _FakeScope:
    def __init__(self, allocator: ISequenceAllocator) -> None:
        self._allocator = allocator

    async def get(self, _type: Any) -> ISequenceAllocator:
        return self._allocator


def test_resolve_group_id_prefers_explicit_envelope_group() -> None:
    env = make_envelope(_Evt(), group_id='explicit')

    assert resolve_group_id(env, partition_by=lambda _m: 'fallback') == GroupId('explicit')


def test_resolve_group_id_falls_back_to_partition_by() -> None:
    env = make_envelope(_Evt())

    assert resolve_group_id(env, partition_by=lambda _m: 'fallback') == GroupId('fallback')


def test_resolve_group_id_is_none_when_keyless() -> None:
    env = make_envelope(_Evt())

    assert resolve_group_id(env, partition_by=None) is None


async def test_resolve_and_allocate_allocates_for_keyed_message() -> None:
    allocator = _FakeAllocator()
    env = make_envelope(_Evt(), group_id='orders')

    group_id, sequence = await resolve_and_allocate(env, None, cast('AsyncContainer', _FakeScope(allocator)))

    assert group_id == GroupId('orders')
    assert sequence == 1
    assert allocator.allocated == [GroupId('orders')]


async def test_resolve_and_allocate_skips_allocation_for_keyless_message() -> None:
    allocator = _FakeAllocator()
    env = make_envelope(_Evt())

    group_id, sequence = await resolve_and_allocate(env, None, cast('AsyncContainer', _FakeScope(allocator)))

    assert group_id is None
    assert sequence is None
    assert allocator.allocated == []


async def test_resolve_and_allocate_uses_partition_by_fallback() -> None:
    allocator = _FakeAllocator()
    env = make_envelope(_Evt())

    group_id, sequence = await resolve_and_allocate(
        env,
        lambda _m: 'derived',
        cast('AsyncContainer', _FakeScope(allocator)),
    )

    assert group_id == GroupId('derived')
    assert sequence == 1
