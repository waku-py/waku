from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from waku.backends.memory import MemoryBackend
from waku.backends.memory._internal.inbox import InMemoryInboxStore
from waku.messaging import MessagingConfig, MessagingModule, OutboxConfig
from waku.messaging._internal.identifiers import EndpointUri, HandlerDestination
from waku.messaging._internal.maintenance import DurabilityMaintenanceLifecycleExtension
from waku.messaging.config import DeadLetterConfig
from waku.messaging.durability import IInboxStore
from waku.messaging.inbox.config import InboxConfig
from waku.messaging.inbox.models import InboxEntry, InboxStatus
from waku.testing import create_test_app

from tests._wait import wait_until


def _maintenance_config() -> MessagingConfig:
    return MessagingConfig(
        outbox=OutboxConfig(),
        inbox=InboxConfig(scheduled_poll_interval=timedelta(seconds=0.01)),
        dead_letter=DeadLetterConfig(auto_replay_enabled=True),
    )


def test_maintenance_lifecycle_extension_registered_no_leadership() -> None:
    # Worker identity at the wiring seam: the single maintenance owner is registered, and the old
    # per-worker DLQ lifecycle extension is gone (its concern folded into the maintenance agent).
    dynamic = MessagingModule.register(_maintenance_config())
    extension_types = {type(ext).__name__ for ext in dynamic.extensions}
    assert 'DurabilityMaintenanceLifecycleExtension' in extension_types
    assert 'DeadLetterLifecycleExtension' not in extension_types
    assert sum(isinstance(ext, DurabilityMaintenanceLifecycleExtension) for ext in dynamic.extensions) == 1


def test_no_maintenance_owner_when_nothing_to_maintain() -> None:
    dynamic = MessagingModule.register(MessagingConfig())
    assert not any(isinstance(ext, DurabilityMaintenanceLifecycleExtension) for ext in dynamic.extensions)


async def test_maintenance_runs_unconditionally_promoting_scheduled_rows() -> None:
    # The no-leader path (leadership unset): booting a memory-backend app starts the maintenance agent
    # unconditionally through the real lifecycle — a due SCHEDULED inbox row gets promoted to INCOMING.
    async with (
        create_test_app(
            imports=[MessagingModule.register(_maintenance_config()), MemoryBackend.register()],
        ) as app,
        app.container() as scope,
    ):
        inbox = await scope.get(IInboxStore)
        assert isinstance(inbox, InMemoryInboxStore)
        due = InboxEntry(
            id=uuid4(),
            payload={'test': True},
            message_type='test.Event',
            source_uri=EndpointUri('local://orders'),
            destination=HandlerDestination('tests.messaging.HandlerA'),
            status=InboxStatus.SCHEDULED,
            execution_time=datetime.now(tz=UTC) - timedelta(minutes=1),
            owner_id=None,
        )
        await inbox.store_incoming(due)

        await wait_until(lambda: any(e.status is InboxStatus.INCOMING for e in inbox.entries.values()))

    assert any(e.status is InboxStatus.INCOMING for e in inbox.entries.values())
