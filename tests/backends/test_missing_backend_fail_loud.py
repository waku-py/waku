from __future__ import annotations

import pytest

from waku.di import object_, scoped
from waku.eventsourcing.modules import EventSourcingConfig, EventSourcingModule
from waku.exceptions import ImproperlyConfiguredError
from waku.messages import IMessage
from waku.messaging import MessagingConfig, MessagingModule, OutboxConfig
from waku.messaging.config import DeadLetterConfig
from waku.messaging.durability import IInboxStore, IOutboxStore
from waku.messaging.inbox.config import InboxConfig
from waku.messaging.router import external_endpoint
from waku.testing import create_test_app
from waku.uow import IUnitOfWork

from tests.messaging.helpers import FakeUoW, RecordingTransport
from tests.messaging.inbox.fake_store import FakeInboxStore
from tests.messaging.outbox.fake_store import FakeOutboxStore


@pytest.mark.parametrize(
    ('config', 'missing_port'),
    [
        pytest.param(MessagingConfig(outbox=OutboxConfig()), 'IOutboxStore', id='outbox'),
        pytest.param(MessagingConfig(inbox=InboxConfig()), 'IInboxStore', id='inbox'),
        pytest.param(MessagingConfig(dead_letter=DeadLetterConfig()), 'IDeadLetterStore', id='dead_letter'),
    ],
)
async def test_durable_messaging_config_without_backend_names_the_fix(
    config: MessagingConfig,
    missing_port: str,
) -> None:
    with pytest.raises(ImproperlyConfiguredError, match=missing_port) as exc_info:
        async with create_test_app(imports=[MessagingModule.register(config)]):
            pass  # pragma: no cover

    assert 'SqlAlchemyBackend.register(' in str(exc_info.value)


async def test_event_sourcing_module_without_backend_names_the_fix() -> None:
    with pytest.raises(ImproperlyConfiguredError, match='IEventStore') as exc_info:
        async with create_test_app(imports=[EventSourcingModule.register(EventSourcingConfig())]):
            pass  # pragma: no cover

    assert 'SqlAlchemyBackend.register(' in str(exc_info.value)


async def test_non_durable_messaging_app_needs_no_backend() -> None:
    async with create_test_app(imports=[MessagingModule.register(MessagingConfig())]):
        pass


def _partition_key(_message: IMessage) -> str:  # pragma: no cover - never called; registration fails first
    return 'k'


async def test_active_inbox_without_allocator_names_the_backend_fix() -> None:
    config = MessagingConfig(inbox=InboxConfig())

    with pytest.raises(ImproperlyConfiguredError, match='ISequenceAllocator') as exc_info:
        async with create_test_app(
            imports=[MessagingModule.register(config)],
            providers=[object_(FakeUoW(), provided_type=IUnitOfWork), scoped(IInboxStore, FakeInboxStore)],
        ):
            pass  # pragma: no cover

    assert 'SqlAlchemyBackend.register(' in str(exc_info.value)


async def test_partition_by_endpoint_without_backend_names_the_fix() -> None:
    config = MessagingConfig(
        endpoints=[external_endpoint('rabbitmq://orders', partition_by=_partition_key)],
        outbox=OutboxConfig(),
        transports={'rabbitmq': RecordingTransport},
    )

    with pytest.raises(ImproperlyConfiguredError, match='ISequenceAllocator') as exc_info:
        async with create_test_app(
            imports=[MessagingModule.register(config)],
            providers=[
                object_(FakeUoW(), provided_type=IUnitOfWork),
                object_(FakeOutboxStore(), provided_type=IOutboxStore),
            ],
        ):
            pass  # pragma: no cover

    assert 'SqlAlchemyBackend.register(' in str(exc_info.value)
