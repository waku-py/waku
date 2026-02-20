from __future__ import annotations

import warnings
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pytest
from typing_extensions import override

from waku.cqrs.contracts.notification import INotification
from waku.eventsourcing.contracts.aggregate import EventSourcedAggregate
from waku.eventsourcing.contracts.event import EventMetadata, IMetadataEnricher
from waku.eventsourcing.exceptions import (
    DuplicateAggregateNameError,
    RegistryFrozenError,
    SnapshotConfigNotFoundError,
    SnapshotMigrationChainError,
    UpcasterChainError,
)
from waku.eventsourcing.modules import (
    CatchUpProjectionBinding,
    EventSourcingConfig,
    EventSourcingExtension,
    EventSourcingModule,
    EventSourcingRegistry,
    EventType,
    SnapshotOptions,
)
from waku.eventsourcing.projection.interfaces import ErrorPolicy, ICatchUpProjection, IProjection
from waku.eventsourcing.repository import EventSourcedRepository
from waku.eventsourcing.serialization.json import JsonEventSerializer
from waku.eventsourcing.serialization.registry import EventTypeRegistry
from waku.eventsourcing.snapshot.migration import ISnapshotMigration, SnapshotMigrationChain
from waku.eventsourcing.snapshot.registry import SnapshotConfig, SnapshotConfigRegistry
from waku.eventsourcing.snapshot.strategy import EventCountStrategy
from waku.eventsourcing.store.in_memory import InMemoryEventStore
from waku.eventsourcing.store.interfaces import IEventStore
from waku.eventsourcing.upcasting import UpcasterChain, add_field, rename_field
from waku.modules import module
from waku.testing import create_test_app

if TYPE_CHECKING:
    from waku.eventsourcing.contracts.event import StoredEvent


@dataclass(frozen=True)
class ItemCreated(INotification):
    name: str


@dataclass(frozen=True)
class ItemRenamed(INotification):
    new_name: str


class Item(EventSourcedAggregate):
    def __init__(self) -> None:  # pragma: no cover
        super().__init__()
        self.name: str = ''

    def create(self, name: str) -> None:  # pragma: no cover
        self._raise_event(ItemCreated(name=name))

    def _apply(self, event: INotification) -> None:  # pragma: no cover
        match event:
            case ItemCreated(name=name):
                self.name = name


class ItemRepository(EventSourcedRepository[Item]):
    pass


async def test_event_sourcing_module_registers_event_store() -> None:
    async with (
        create_test_app(
            imports=[EventSourcingModule.register()],
        ) as app,
        app.container() as container,
    ):
        store = await container.get(IEventStore)
        assert isinstance(store, InMemoryEventStore)


async def test_event_sourcing_module_with_custom_config() -> None:
    config = EventSourcingConfig(store=InMemoryEventStore)
    async with (
        create_test_app(
            imports=[EventSourcingModule.register(config)],
        ) as app,
        app.container() as container,
    ):
        store = await container.get(IEventStore)
        assert isinstance(store, InMemoryEventStore)


async def test_event_sourcing_extension_binds_repository() -> None:
    es_ext = EventSourcingExtension().bind_aggregate(repository=ItemRepository)

    @module(
        imports=[EventSourcingModule.register()],
        extensions=[es_ext],
    )
    class ItemModule:
        pass

    async with (
        create_test_app(
            imports=[ItemModule],
        ) as app,
        app.container() as container,
    ):
        repo = await container.get(ItemRepository)
        assert isinstance(repo, ItemRepository)


async def test_event_type_descriptor_with_custom_name_and_aliases() -> None:
    es_ext = EventSourcingExtension().bind_aggregate(
        repository=ItemRepository,
        event_types=[
            ItemCreated,
            EventType(ItemRenamed, name='item_renamed', aliases=['item_renamed_v0']),
        ],
    )

    @module(
        imports=[EventSourcingModule.register()],
        extensions=[es_ext],
    )
    class ItemModule:
        pass

    async with (
        create_test_app(imports=[ItemModule]) as app,
        app.container() as container,
    ):
        registry = await container.get(EventTypeRegistry)

        assert 'ItemCreated' in registry
        assert registry.resolve('ItemCreated') is ItemCreated

        assert 'item_renamed' in registry
        assert registry.resolve('item_renamed') is ItemRenamed
        assert registry.get_name(ItemRenamed) == 'item_renamed'

        assert 'item_renamed_v0' in registry
        assert registry.resolve('item_renamed_v0') is ItemRenamed


class SearchIndexProjection(ICatchUpProjection):
    projection_name = 'search_index'

    async def project(self, events: Sequence[StoredEvent], /) -> None:  # pragma: no cover
        pass


class ItemListProjection(IProjection):
    projection_name = 'item_list'

    async def project(self, events: Sequence[StoredEvent], /) -> None:  # pragma: no cover
        pass


async def test_catch_up_projections_registered_via_binding() -> None:
    es_ext = EventSourcingExtension()
    es_ext.bind_aggregate(repository=ItemRepository, event_types=[ItemCreated])
    es_ext.bind_catch_up_projections([
        CatchUpProjectionBinding(
            projection=SearchIndexProjection,
            error_policy=ErrorPolicy.SKIP,
            max_retry_attempts=3,
        ),
    ])

    @module(
        imports=[EventSourcingModule.register()],
        extensions=[es_ext],
    )
    class TestItemModule:
        pass

    async with create_test_app(imports=[TestItemModule]) as app, app.container() as container:
        projections = await container.get(Sequence[ICatchUpProjection])
        assert len(projections) == 1
        assert isinstance(projections[0], SearchIndexProjection)


async def test_catch_up_projections_bare_type_uses_defaults() -> None:
    es_ext = EventSourcingExtension()
    es_ext.bind_aggregate(repository=ItemRepository, event_types=[ItemCreated])
    es_ext.bind_catch_up_projections([SearchIndexProjection])

    @module(
        imports=[EventSourcingModule.register()],
        extensions=[es_ext],
    )
    class TestItemModule:
        pass

    async with create_test_app(imports=[TestItemModule]) as app, app.container() as container:
        projections = await container.get(Sequence[ICatchUpProjection])
        assert len(projections) == 1
        assert isinstance(projections[0], SearchIndexProjection)


async def test_no_catch_up_projections_resolves_empty_sequence() -> None:
    es_ext = EventSourcingExtension()
    es_ext.bind_aggregate(repository=ItemRepository, event_types=[ItemCreated])

    @module(
        imports=[EventSourcingModule.register()],
        extensions=[es_ext],
    )
    class TestItemModule:
        pass

    async with create_test_app(imports=[TestItemModule]) as app, app.container() as container:
        projections = await container.get(Sequence[ICatchUpProjection])
        assert len(projections) == 0


async def test_catch_up_and_inline_projections_independent() -> None:
    es_ext = EventSourcingExtension()
    es_ext.bind_aggregate(repository=ItemRepository, event_types=[ItemCreated], projections=[ItemListProjection])
    es_ext.bind_catch_up_projections([SearchIndexProjection])

    @module(
        imports=[EventSourcingModule.register()],
        extensions=[es_ext],
    )
    class TestItemModule:
        pass

    async with create_test_app(imports=[TestItemModule]) as app, app.container() as container:
        inline_projections = await container.get(Sequence[IProjection])
        assert len(inline_projections) == 1
        assert isinstance(inline_projections[0], ItemListProjection)

        catch_up_projections = await container.get(Sequence[ICatchUpProjection])
        assert len(catch_up_projections) == 1
        assert isinstance(catch_up_projections[0], SearchIndexProjection)


class TraceIdEnricher(IMetadataEnricher):
    @override
    def enrich(self, metadata: EventMetadata, /) -> EventMetadata:  # pragma: no cover
        return metadata


async def test_enrichers_registered_via_config() -> None:
    config = EventSourcingConfig(enrichers=[TraceIdEnricher])

    async with (
        create_test_app(imports=[EventSourcingModule.register(config)]) as app,
        app.container() as container,
    ):
        enrichers = await container.get(Sequence[IMetadataEnricher])
        assert len(enrichers) == 1
        assert isinstance(enrichers[0], TraceIdEnricher)


async def test_no_enrichers_resolves_empty_sequence() -> None:
    async with (
        create_test_app(imports=[EventSourcingModule.register()]) as app,
        app.container() as container,
    ):
        enrichers = await container.get(Sequence[IMetadataEnricher])
        assert len(enrichers) == 0


async def test_event_type_with_version_and_upcasters() -> None:
    es_ext = EventSourcingExtension().bind_aggregate(
        repository=ItemRepository,
        event_types=[
            EventType(
                ItemCreated,
                version=2,
                upcasters=[rename_field(from_version=1, old='name', new='full_name')],
            ),
        ],
    )

    @module(
        imports=[EventSourcingModule.register()],
        extensions=[es_ext],
    )
    class ItemModule:
        pass

    async with create_test_app(imports=[ItemModule]) as app, app.container() as container:
        registry = await container.get(EventTypeRegistry)
        assert registry.get_version(ItemCreated) == 2

        chain = await container.get(UpcasterChain)
        result = chain.upcast('ItemCreated', {'name': 'Widget'}, schema_version=1)
        assert result == {'full_name': 'Widget'}


async def test_upcaster_from_version_gte_event_version_raises() -> None:
    es_ext = EventSourcingExtension().bind_aggregate(
        repository=ItemRepository,
        event_types=[
            EventType(
                ItemCreated,
                version=2,
                upcasters=[add_field(from_version=2, field='x', default=0)],
            ),
        ],
    )

    @module(
        imports=[EventSourcingModule.register()],
        extensions=[es_ext],
    )
    class ItemModule:
        pass

    with pytest.raises(UpcasterChainError, match=r'from_version .* must be < event version'):
        async with create_test_app(imports=[ItemModule]):
            pass  # pragma: no cover


class ItemLog(EventSourcedAggregate):
    def __init__(self) -> None:  # pragma: no cover
        super().__init__()
        self.entries: list[str] = []

    def _apply(self, event: INotification) -> None:  # pragma: no cover
        match event:
            case ItemCreated(name=name):
                self.entries.append(name)


class ItemLogRepository(EventSourcedRepository[ItemLog]):
    pass


async def test_same_event_type_across_two_modules() -> None:
    producer_ext = EventSourcingExtension().bind_aggregate(
        repository=ItemRepository,
        event_types=[ItemCreated],
    )
    consumer_ext = EventSourcingExtension().bind_aggregate(
        repository=ItemLogRepository,
        event_types=[ItemCreated],
    )

    @module(
        imports=[EventSourcingModule.register()],
        extensions=[producer_ext],
    )
    class ProducerModule:
        pass

    @module(
        extensions=[consumer_ext],
    )
    class ConsumerModule:
        pass

    async with (
        create_test_app(imports=[ProducerModule, ConsumerModule]) as app,
        app.container() as container,
    ):
        registry = await container.get(EventTypeRegistry)
        assert registry.resolve('ItemCreated') is ItemCreated
        assert registry.get_version(ItemCreated) == 1


async def test_same_event_type_with_aliases_across_two_modules() -> None:
    shared_event = EventType(ItemCreated, aliases=['item_created_v0'])

    producer_ext = EventSourcingExtension().bind_aggregate(
        repository=ItemRepository,
        event_types=[shared_event],
    )
    consumer_ext = EventSourcingExtension().bind_aggregate(
        repository=ItemLogRepository,
        event_types=[shared_event],
    )

    @module(
        imports=[EventSourcingModule.register()],
        extensions=[producer_ext],
    )
    class ProducerModule:
        pass

    @module(
        extensions=[consumer_ext],
    )
    class ConsumerModule:
        pass

    async with (
        create_test_app(imports=[ProducerModule, ConsumerModule]) as app,
        app.container() as container,
    ):
        registry = await container.get(EventTypeRegistry)
        assert registry.resolve('ItemCreated') is ItemCreated
        assert registry.resolve('item_created_v0') is ItemCreated


async def test_same_upcasters_across_two_modules() -> None:
    shared_event = EventType(
        ItemCreated,
        version=2,
        upcasters=[rename_field(from_version=1, old='name', new='full_name')],
    )

    producer_ext = EventSourcingExtension().bind_aggregate(
        repository=ItemRepository,
        event_types=[shared_event],
    )
    consumer_ext = EventSourcingExtension().bind_aggregate(
        repository=ItemLogRepository,
        event_types=[shared_event],
    )

    @module(
        imports=[EventSourcingModule.register()],
        extensions=[producer_ext],
    )
    class ProducerModule:
        pass

    @module(
        extensions=[consumer_ext],
    )
    class ConsumerModule:
        pass

    async with (
        create_test_app(imports=[ProducerModule, ConsumerModule]) as app,
        app.container() as container,
    ):
        chain = await container.get(UpcasterChain)
        result = chain.upcast('ItemCreated', {'name': 'Widget'}, schema_version=1)
        assert result == {'full_name': 'Widget'}


async def test_conflicting_upcasters_across_modules_raises() -> None:
    producer_ext = EventSourcingExtension().bind_aggregate(
        repository=ItemRepository,
        event_types=[
            EventType(
                ItemCreated,
                version=2,
                upcasters=[rename_field(from_version=1, old='name', new='full_name')],
            ),
        ],
    )
    consumer_ext = EventSourcingExtension().bind_aggregate(
        repository=ItemLogRepository,
        event_types=[
            EventType(
                ItemCreated,
                version=2,
                upcasters=[add_field(from_version=1, field='x', default=0)],
            ),
        ],
    )

    @module(
        imports=[EventSourcingModule.register()],
        extensions=[producer_ext],
    )
    class ProducerModule:
        pass

    @module(
        extensions=[consumer_ext],
    )
    class ConsumerModule:
        pass

    with pytest.raises(UpcasterChainError, match='Conflicting upcaster definitions'):
        async with create_test_app(imports=[ProducerModule, ConsumerModule]):
            pass  # pragma: no cover


async def test_empty_upcaster_chain_always_registered() -> None:
    es_ext = EventSourcingExtension().bind_aggregate(
        repository=ItemRepository,
        event_types=[ItemCreated],
    )

    @module(
        imports=[EventSourcingModule.register()],
        extensions=[es_ext],
    )
    class ItemModule:
        pass

    async with create_test_app(imports=[ItemModule]) as app, app.container() as container:
        chain = await container.get(UpcasterChain)
        result = chain.upcast('ItemCreated', {'name': 'Widget'}, schema_version=1)
        assert result == {'name': 'Widget'}


class DuplicateItemRepository(EventSourcedRepository[Item]):
    aggregate_name = 'Item'


async def test_duplicate_aggregate_name_across_modules_raises() -> None:
    ext_a = EventSourcingExtension().bind_aggregate(repository=ItemRepository)
    ext_b = EventSourcingExtension().bind_aggregate(repository=DuplicateItemRepository)

    @module(
        imports=[EventSourcingModule.register()],
        extensions=[ext_a],
    )
    class ModuleA:
        pass

    @module(extensions=[ext_b])
    class ModuleB:
        pass

    with pytest.raises(DuplicateAggregateNameError, match='Item'):
        async with create_test_app(imports=[ModuleA, ModuleB]):
            pass  # pragma: no cover


async def test_different_aggregate_names_across_modules_passes() -> None:
    ext_a = EventSourcingExtension().bind_aggregate(repository=ItemRepository)
    ext_b = EventSourcingExtension().bind_aggregate(repository=ItemLogRepository)

    @module(
        imports=[EventSourcingModule.register()],
        extensions=[ext_a],
    )
    class ModuleA:
        pass

    @module(extensions=[ext_b])
    class ModuleB:
        pass

    async with create_test_app(imports=[ModuleA, ModuleB]):
        pass


async def test_snapshot_config_registry_resolvable_with_strategy() -> None:
    es_ext = EventSourcingExtension().bind_aggregate(
        repository=ItemRepository,
        snapshot=SnapshotOptions(strategy=EventCountStrategy(threshold=50)),
    )

    @module(
        imports=[EventSourcingModule.register()],
        extensions=[es_ext],
    )
    class ItemModule:
        pass

    async with create_test_app(imports=[ItemModule]) as app, app.container() as container:
        registry = await container.get(SnapshotConfigRegistry)
        config = registry.get('Item')

        assert isinstance(config, SnapshotConfig)
        assert isinstance(config.strategy, EventCountStrategy)
        assert config.schema_version == 1


async def test_snapshot_config_registry_empty_when_no_strategy() -> None:
    es_ext = EventSourcingExtension().bind_aggregate(
        repository=ItemRepository,
    )

    @module(
        imports=[EventSourcingModule.register()],
        extensions=[es_ext],
    )
    class ItemModule:
        pass

    async with create_test_app(imports=[ItemModule]) as app, app.container() as container:
        registry = await container.get(SnapshotConfigRegistry)

        with pytest.raises(SnapshotConfigNotFoundError, match='Item'):
            registry.get('Item')


class _NoOpSnapshotMigration(ISnapshotMigration):
    from_version = 1
    to_version = 2

    @override
    def migrate(self, state: dict[str, Any], /) -> dict[str, Any]:  # pragma: no cover
        return state


class _V2ToV3SnapshotMigration(ISnapshotMigration):
    from_version = 2
    to_version = 3

    @override
    def migrate(self, state: dict[str, Any], /) -> dict[str, Any]:  # pragma: no cover
        return state


async def test_snapshot_config_registry_with_schema_version_and_migrations() -> None:
    es_ext = EventSourcingExtension().bind_aggregate(
        repository=ItemRepository,
        snapshot=SnapshotOptions(
            strategy=EventCountStrategy(threshold=50),
            schema_version=2,
            migrations=[_NoOpSnapshotMigration()],
        ),
    )

    @module(
        imports=[EventSourcingModule.register()],
        extensions=[es_ext],
    )
    class ItemModule:
        pass

    async with create_test_app(imports=[ItemModule]) as app, app.container() as container:
        registry = await container.get(SnapshotConfigRegistry)
        config = registry.get('Item')

        assert config.schema_version == 2
        assert isinstance(config.migration_chain, SnapshotMigrationChain)


async def test_snapshot_migration_target_rejects_schema_version_without_migrations() -> None:
    es_ext = EventSourcingExtension().bind_aggregate(
        repository=ItemRepository,
        snapshot=SnapshotOptions(
            strategy=EventCountStrategy(threshold=50),
            schema_version=3,
        ),
    )

    @module(
        imports=[EventSourcingModule.register()],
        extensions=[es_ext],
    )
    class ItemModule:
        pass

    with pytest.raises(SnapshotMigrationChainError, match='schema_version is 3 but no migrations are provided'):
        async with create_test_app(imports=[ItemModule]):
            pass  # pragma: no cover


async def test_snapshot_migration_target_rejects_chain_not_reaching_schema_version() -> None:
    es_ext = EventSourcingExtension().bind_aggregate(
        repository=ItemRepository,
        snapshot=SnapshotOptions(
            strategy=EventCountStrategy(threshold=50),
            schema_version=3,
            migrations=[_NoOpSnapshotMigration()],
        ),
    )

    @module(
        imports=[EventSourcingModule.register()],
        extensions=[es_ext],
    )
    class ItemModule:
        pass

    with pytest.raises(SnapshotMigrationChainError, match='migration chain reaches version 2 but schema_version is 3'):
        async with create_test_app(imports=[ItemModule]):
            pass  # pragma: no cover


async def test_snapshot_migration_target_rejects_chain_not_starting_at_version_1() -> None:
    es_ext = EventSourcingExtension().bind_aggregate(
        repository=ItemRepository,
        snapshot=SnapshotOptions(
            strategy=EventCountStrategy(threshold=50),
            schema_version=3,
            migrations=[_V2ToV3SnapshotMigration()],
        ),
    )

    @module(
        imports=[EventSourcingModule.register()],
        extensions=[es_ext],
    )
    class ItemModule:
        pass

    with pytest.raises(SnapshotMigrationChainError, match='starts at version 2 but must start at version 1'):
        async with create_test_app(imports=[ItemModule]):
            pass  # pragma: no cover


async def test_warns_when_serializer_configured_but_no_event_types() -> None:
    config = EventSourcingConfig(event_serializer=JsonEventSerializer)

    @module(imports=[EventSourcingModule.register(config)])
    class EmptyModule:
        pass

    with pytest.warns(UserWarning, match='A serializer is configured but no event types were registered'):
        async with create_test_app(imports=[EmptyModule]):
            pass


async def test_no_warning_when_serializer_configured_with_event_types() -> None:
    config = EventSourcingConfig(event_serializer=JsonEventSerializer)
    es_ext = EventSourcingExtension().bind_aggregate(
        repository=ItemRepository,
        event_types=[ItemCreated],
    )

    @module(imports=[EventSourcingModule.register(config)], extensions=[es_ext])
    class ItemModule:
        pass

    with warnings.catch_warnings():
        warnings.simplefilter('error')
        async with create_test_app(imports=[ItemModule]):
            pass


def test_frozen_registry_rejects_merge() -> None:
    registry = EventSourcingRegistry()
    registry.freeze()

    with pytest.raises(RegistryFrozenError):
        registry.merge(EventSourcingRegistry())
