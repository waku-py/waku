from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Self

from typing_extensions import override

from waku.di import ProviderSpec, WithParents, many, object_, scoped
from waku.eventsourcing.projection.interfaces import IProjection
from waku.eventsourcing.serialization.interfaces import IEventSerializer
from waku.eventsourcing.serialization.registry import EventTypeRegistry
from waku.eventsourcing.snapshot.interfaces import ISnapshotStore, ISnapshotStrategy
from waku.eventsourcing.store.in_memory import InMemoryEventStore
from waku.eventsourcing.store.interfaces import IEventStore
from waku.eventsourcing.store.sqlalchemy.store import (
    EventStoreTables,  # Runtime access for DI registration
)
from waku.extensions import OnModuleConfigure, OnModuleRegistration
from waku.modules import DynamicModule, ModuleMetadataRegistry, module

if TYPE_CHECKING:
    from waku.eventsourcing.repository import EventSourcedRepository
    from waku.modules import ModuleMetadata, ModuleType

__all__ = [
    'EventSourcingConfig',
    'EventSourcingExtension',
    'EventSourcingModule',
]


@dataclass(frozen=True, slots=True, kw_only=True)
class EventSourcingConfig:
    event_store_type: type[IEventStore] = InMemoryEventStore
    serializer_type: type[IEventSerializer] | None = None
    snapshot_store_type: type[ISnapshotStore] | None = None
    snapshot_strategy: ISnapshotStrategy | None = None
    event_store_tables: EventStoreTables | None = None


@module()
class EventSourcingModule:
    @classmethod
    def register(cls, config: EventSourcingConfig | None = None, /) -> DynamicModule:
        config_ = config or EventSourcingConfig()
        providers: list[ProviderSpec] = [
            scoped(WithParents[IEventStore], config_.event_store_type),  # ty:ignore[not-subscriptable]
        ]
        if config_.serializer_type is not None:
            providers.append(scoped(IEventSerializer, config_.serializer_type))
        if config_.snapshot_store_type is not None:
            providers.append(scoped(ISnapshotStore, config_.snapshot_store_type))
        if config_.snapshot_strategy is not None:
            providers.append(object_(config_.snapshot_strategy, provided_type=ISnapshotStrategy))
        if config_.event_store_tables is not None:
            providers.append(object_(config_.event_store_tables, provided_type=EventStoreTables))
        return DynamicModule(
            parent_module=cls,
            providers=providers,
            extensions=[EventTypeRegistryAggregator(), ProjectionAggregator()],
            is_global=True,
        )


class EventSourcingExtension(OnModuleConfigure):
    def __init__(self) -> None:
        self._repository_types: list[tuple[type[EventSourcedRepository[Any]], type[EventSourcedRepository[Any]]]] = []
        self._event_registry = EventTypeRegistry()
        self._projection_types: list[type[Any]] = []

    def bind_repository(self, repository_type: type[EventSourcedRepository[Any]]) -> Self:
        self._repository_types.append((repository_type, repository_type))
        return self

    def register_events(self, *event_types: type[Any]) -> Self:
        for event_type in event_types:
            self._event_registry.register(event_type)
        return self

    def bind_projection(self, projection_type: type[Any]) -> Self:
        self._projection_types.append(projection_type)
        return self

    @property
    def event_registry(self) -> EventTypeRegistry:
        return self._event_registry

    @property
    def projection_types(self) -> list[type[Any]]:
        return list(self._projection_types)

    def on_module_configure(self, metadata: ModuleMetadata) -> None:
        for base_type, impl_type in self._repository_types:
            metadata.providers.append(scoped(WithParents[base_type], impl_type))  # ty:ignore[not-subscriptable]


class EventTypeRegistryAggregator(OnModuleRegistration):
    @override
    def on_module_registration(
        self,
        registry: ModuleMetadataRegistry,
        owning_module: ModuleType,
        context: Mapping[Any, Any] | None,
    ) -> None:
        aggregated = EventTypeRegistry()
        for _module_type, ext in registry.find_extensions(EventSourcingExtension):
            aggregated.merge(ext.event_registry)
        aggregated.freeze()
        registry.add_provider(owning_module, object_(aggregated))


class ProjectionAggregator(OnModuleRegistration):
    @override
    def on_module_registration(
        self,
        registry: ModuleMetadataRegistry,
        owning_module: ModuleType,
        context: Mapping[Any, Any] | None,
    ) -> None:
        all_projection_types: list[type[Any]] = []
        for _module_type, ext in registry.find_extensions(EventSourcingExtension):
            all_projection_types.extend(ext.projection_types)

        if all_projection_types:
            registry.add_provider(owning_module, many(IProjection, *all_projection_types))
        else:
            registry.add_provider(owning_module, object_((), provided_type=Sequence[IProjection]))
