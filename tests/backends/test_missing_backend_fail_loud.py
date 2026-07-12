from __future__ import annotations

import pytest

from waku.eventsourcing.modules import EventSourcingConfig, EventSourcingModule
from waku.exceptions import ImproperlyConfiguredError
from waku.messaging import MessagingConfig, MessagingModule, OutboxConfig
from waku.messaging.config import DeadLetterConfig
from waku.messaging.inbox.config import InboxConfig
from waku.testing import create_test_app


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
