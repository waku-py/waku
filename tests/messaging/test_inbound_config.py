import pytest
from typing_extensions import override

from waku._internal.sentinel import MISSING  # noqa: PLC2701
from waku.di import object_
from waku.messaging.config import InboundConfig, MessagingConfig
from waku.messaging.contracts.message import IMessage
from waku.messaging.endpoints.base import listen
from waku.messaging.exceptions import ImproperlyConfiguredError
from waku.messaging.inbox.config import InboxConfig
from waku.messaging.modules import MessagingModule
from waku.messaging.transport.inbound import ConsumeCallback, IInboundTransport
from waku.testing import create_test_app
from waku.uow import IUnitOfWork

from tests.messaging.helpers import FakeUoW
from tests.messaging.inbox.fake_store import FakeInboxStore


class _StubInboundTransport(IInboundTransport):
    @override
    def subscribe(self, queue: str, on_message: ConsumeCallback) -> None: ...

    @override
    async def start(self) -> None: ...

    @override
    async def stop(self) -> None: ...


def _partition_key(_message: IMessage) -> str:
    return 'k'


def test_listen_builds_entry_with_inherited_requeue() -> None:
    e = listen('orders')
    assert e.uri == 'orders'
    assert e.max_requeue_attempts is MISSING  # type: ignore[comparison-overlap]  # mypy lacks PEP 661 sentinel support


def test_listen_builds_entry_with_explicit_requeue() -> None:
    e = listen('orders', max_requeue_attempts=3)
    assert e.uri == 'orders'
    assert e.max_requeue_attempts == 3


def test_inbound_without_inbox_raises() -> None:
    config = MessagingConfig(
        inbound=InboundConfig(
            transport=_StubInboundTransport,
            listeners=[listen('q')],
        ),
    )
    with pytest.raises(ImproperlyConfiguredError, match='inbound listeners require inbox'):
        MessagingModule.register(config)


def test_inbound_with_no_listeners_raises() -> None:
    config = MessagingConfig(
        inbound=InboundConfig(transport=_StubInboundTransport, listeners=[]),
    )
    with pytest.raises(ImproperlyConfiguredError, match='at least one listener'):
        MessagingModule.register(config)


async def test_inbound_partition_by_without_allocator_raises_at_startup() -> None:
    inbox = FakeInboxStore()
    config = MessagingConfig(
        inbox=InboxConfig(store=lambda: inbox, owner_id='test-node:1'),
        inbound=InboundConfig(
            transport=_StubInboundTransport,
            listeners=[listen('orders', partition_by=_partition_key)],
        ),
    )
    with pytest.raises(ImproperlyConfiguredError, match='ISequenceAllocator'):
        async with create_test_app(
            imports=[MessagingModule.register(config)],
            providers=[object_(FakeUoW(), provided_type=IUnitOfWork)],
        ):
            pass  # pragma: no cover
