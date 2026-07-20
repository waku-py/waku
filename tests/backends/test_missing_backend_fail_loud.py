from __future__ import annotations

from typing import ClassVar

import pytest
from typing_extensions import override

from waku import INodeRegistry, NodeIdentity, NodeRegistryConfig
from waku.backends.memory import MemoryBackend
from waku.backends.memory._internal.nodes import InMemoryNodeRegistry
from waku.di import object_, scoped
from waku.eventsourcing.modules import EventSourcingConfig, EventSourcingModule
from waku.exceptions import ImproperlyConfiguredError
from waku.messages import IEvent, IMessage
from waku.messaging import EventHandler, MessagingConfig, MessagingExtension, MessagingModule, OutboxConfig
from waku.messaging.config import DeadLetterConfig
from waku.messaging.durability import IInboxStore, IOutboxStore
from waku.messaging.errors.policy import ErrorPolicy
from waku.messaging.inbox.config import InboxConfig
from waku.messaging.router import external_endpoint
from waku.testing import create_test_app
from waku.uow import IUnitOfWork

from tests.messaging.helpers import RecordingTransport, RecordingUoW, durability_providers
from tests.messaging.inbox.fake_store import FakeInboxStore
from tests.messaging.outbox.fake_store import RecordingOutboxStore


@pytest.mark.parametrize(
    'config',
    [
        pytest.param(MessagingConfig(outbox=OutboxConfig()), id='outbox'),
        pytest.param(MessagingConfig(inbox=InboxConfig()), id='inbox'),
        pytest.param(MessagingConfig(dead_letter=DeadLetterConfig()), id='dead_letter'),
    ],
)
async def test_durable_messaging_config_without_backend_names_the_fix(
    config: MessagingConfig,
) -> None:
    with pytest.raises(ImproperlyConfiguredError, match='IDurabilityStore') as exc_info:
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


async def test_active_inbox_without_backend_names_the_backend_fix() -> None:
    config = MessagingConfig(inbox=InboxConfig())

    with pytest.raises(ImproperlyConfiguredError, match='IDurabilityStore') as exc_info:
        async with create_test_app(
            imports=[MessagingModule.register(config)],
            providers=[object_(RecordingUoW(), provided_type=IUnitOfWork), scoped(IInboxStore, FakeInboxStore)],
        ):
            pass  # pragma: no cover

    assert 'SqlAlchemyBackend.register(' in str(exc_info.value)


async def test_durability_without_node_registry_fails_loud_at_wiring() -> None:
    # No degraded mode: durable rows are owned by a node, so an app that can write them without a
    # membership oracle is misassembled. Everything else is provided — only the registry is missing.
    with pytest.raises(ImproperlyConfiguredError, match='INodeRegistry') as exc_info:
        async with create_test_app(
            imports=[MessagingModule.register(MessagingConfig(outbox=OutboxConfig()))],
            providers=durability_providers(
                with_node_registry=False,
                extra=[object_(NodeRegistryConfig(), provided_type=NodeRegistryConfig)],
            ),
        ):
            pass  # pragma: no cover

    assert 'SqlAlchemyBackend.register(' in str(exc_info.value)


async def test_durability_without_node_registry_config_fails_loud_at_wiring() -> None:
    # The pair is indivisible, exactly as ILease + LeaseConfig are: an oracle whose heartbeat cadence
    # nobody published cannot be driven, so half a registry is as misassembled as none.
    with pytest.raises(ImproperlyConfiguredError, match='NodeRegistryConfig') as exc_info:
        async with create_test_app(
            imports=[MessagingModule.register(MessagingConfig(outbox=OutboxConfig()))],
            providers=durability_providers(
                with_node_registry=False,
                extra=[object_(InMemoryNodeRegistry(), provided_type=INodeRegistry)],
            ),
        ):
            pass  # pragma: no cover

    assert 'SqlAlchemyBackend.register(' in str(exc_info.value)


class _PolicyDrivenEvent(IEvent):
    pass


class _MoveToDeadLetterHandler(EventHandler[_PolicyDrivenEvent]):
    error_policies: ClassVar = (ErrorPolicy.on_any_exception().move_to_dead_letter(),)

    @override
    async def handle(self, event: _PolicyDrivenEvent, /) -> None: ...  # pragma: no cover - rejected at wiring


async def test_policy_driven_dead_letter_without_node_registry_fails_loud_at_wiring() -> None:
    # A handler policy alone activates the dead-letter store, with no dead_letter sub-config in sight.
    # Such an app writes durable rows stamped with its NodeIdentity, so it owes the same membership
    # oracle as a config-declared one — the membership gate must key on the same handler-aware
    # predicate the store gate does, not on the narrower config-only one.
    with pytest.raises(ImproperlyConfiguredError, match='INodeRegistry') as exc_info:
        async with create_test_app(
            imports=[MessagingModule.register(MessagingConfig())],
            providers=durability_providers(with_node_registry=False),
            extensions=[MessagingExtension().bind(_MoveToDeadLetterHandler)],
        ):
            pass  # pragma: no cover

    assert 'SqlAlchemyBackend.register(' in str(exc_info.value)


async def test_policy_driven_dead_letter_app_registers_its_node() -> None:
    # The positive half of the same law: once the registry is published, the node is a member.
    async with create_test_app(
        imports=[MessagingModule.register(MessagingConfig()), MemoryBackend.register()],
        extensions=[MessagingExtension().bind(_MoveToDeadLetterHandler)],
    ) as app:
        identity = await app.container.get(NodeIdentity)
        async with app.container() as scope:
            members = await (await scope.get(INodeRegistry)).load_all()

    assert [registration.node_id for registration in members] == [identity.node_id]


async def test_partition_by_endpoint_without_backend_names_the_fix() -> None:
    config = MessagingConfig(
        endpoints=[external_endpoint('rabbitmq://orders', partition_by=_partition_key)],
        outbox=OutboxConfig(),
        transports={'rabbitmq': RecordingTransport},
    )

    with pytest.raises(ImproperlyConfiguredError, match='IDurabilityStore') as exc_info:
        async with create_test_app(
            imports=[MessagingModule.register(config)],
            providers=[
                object_(RecordingUoW(), provided_type=IUnitOfWork),
                object_(RecordingOutboxStore(), provided_type=IOutboxStore),
            ],
        ):
            pass  # pragma: no cover

    assert 'SqlAlchemyBackend.register(' in str(exc_info.value)
