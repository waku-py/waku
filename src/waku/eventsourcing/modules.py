from __future__ import annotations

import warnings
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Self, TypeAlias

from typing_extensions import override

from waku.di import ProviderSpec, WithParents, many, object_, scoped
from waku.eventsourcing.projection.interfaces import IProjection
from waku.eventsourcing.serialization.interfaces import IEventSerializer
from waku.eventsourcing.serialization.registry import EventTypeRegistry
from waku.eventsourcing.snapshot.interfaces import ISnapshotStore, ISnapshotStrategy
from waku.eventsourcing.snapshot.serialization import ISnapshotStateSerializer
from waku.eventsourcing.store.in_memory import InMemoryEventStore
from waku.eventsourcing.store.interfaces import IEventStore
from waku.extensions import OnModuleConfigure, OnModuleRegistration
from waku.modules import DynamicModule, ModuleMetadataRegistry, module

if TYPE_CHECKING:
    from waku.cqrs.contracts.notification import INotification
    from waku.eventsourcing.repository import EventSourcedRepository
    from waku.modules import ModuleMetadata, ModuleType

__all__ = [
    'EventSourcingConfig',
    'EventSourcingExtension',
    'EventSourcingModule',
    'EventType',
    'EventTypeSpec',
]


@dataclass(frozen=True, slots=True)
class EventType:
    event_type: type[INotification]
    name: str | None = field(default=None, kw_only=True)
    aliases: Sequence[str] = field(default=(), kw_only=True)


EventTypeSpec: TypeAlias = 'type[INotification] | EventType'


@dataclass(frozen=True, slots=True, kw_only=True)
class AggregateBinding:
    repository: type[EventSourcedRepository[Any]]
    event_types: Sequence[EventTypeSpec] = ()
    projections: Sequence[type[Any]] = ()
    snapshot_strategy: ISnapshotStrategy | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class EventSourcingConfig:
    store: type[IEventStore] | None = None
    event_serializer: type[IEventSerializer] | None = None
    snapshot_store: type[ISnapshotStore] | None = None
    snapshot_state_serializer: type[ISnapshotStateSerializer] | None = None
    store_factory: Callable[..., IEventStore] | None = None
    snapshot_store_factory: Callable[..., ISnapshotStore] | None = None

    def __post_init__(self) -> None:
        if self.store is not None and self.store_factory is not None:
            msg = 'Cannot set both store and store_factory'
            raise ValueError(msg)
        if self.snapshot_store is not None and self.snapshot_store_factory is not None:
            msg = 'Cannot set both snapshot_store and snapshot_store_factory'
            raise ValueError(msg)


@module()
class EventSourcingModule:
    @classmethod
    def register(cls, config: EventSourcingConfig | None = None, /) -> DynamicModule:
        config_ = config or EventSourcingConfig()
        providers: list[ProviderSpec] = []

        if config_.store_factory is not None:
            providers.append(scoped(IEventStore, config_.store_factory))
        else:
            store_type = config_.store or InMemoryEventStore
            providers.append(scoped(WithParents[IEventStore], store_type))  # ty:ignore[not-subscriptable]

        if config_.event_serializer is not None:
            providers.append(scoped(IEventSerializer, config_.event_serializer))

        if config_.snapshot_store_factory is not None:
            providers.append(scoped(ISnapshotStore, config_.snapshot_store_factory))
        elif config_.snapshot_store is not None:
            providers.append(scoped(ISnapshotStore, config_.snapshot_store))

        if config_.snapshot_state_serializer is not None:
            providers.append(scoped(ISnapshotStateSerializer, config_.snapshot_state_serializer))

        return DynamicModule(
            parent_module=cls,
            providers=providers,
            extensions=[
                EventTypeRegistryAggregator(has_serializer=config_.event_serializer is not None),
                ProjectionAggregator(),
            ],
            is_global=True,
        )


@dataclass
class EventSourcingExtension(OnModuleConfigure):
    _bindings: list[AggregateBinding] = field(default_factory=list, init=False)

    def bind_aggregate(
        self,
        repository: type[EventSourcedRepository[Any]],
        event_types: Sequence[EventTypeSpec] = (),
        projections: Sequence[type[Any]] = (),
        snapshot_strategy: ISnapshotStrategy | None = None,
    ) -> Self:
        self._bindings.append(
            AggregateBinding(
                repository=repository,
                event_types=event_types,
                projections=projections,
                snapshot_strategy=snapshot_strategy,
            )
        )
        return self

    @property
    def bindings(self) -> Sequence[AggregateBinding]:
        return list(self._bindings)

    def on_module_configure(self, metadata: ModuleMetadata) -> None:
        for binding in self._bindings:
            repo_type = binding.repository
            metadata.providers.append(scoped(WithParents[repo_type], repo_type))  # type: ignore[misc,valid-type]
            if binding.snapshot_strategy is not None:
                metadata.providers.append(object_(binding.snapshot_strategy, provided_type=ISnapshotStrategy))


class EventTypeRegistryAggregator(OnModuleRegistration):
    def __init__(self, *, has_serializer: bool = False) -> None:
        self._has_serializer = has_serializer

    @override
    def on_module_registration(
        self,
        registry: ModuleMetadataRegistry,
        owning_module: ModuleType,
        context: Mapping[Any, Any] | None,
    ) -> None:
        aggregated = EventTypeRegistry()
        for _module_type, ext in registry.find_extensions(EventSourcingExtension):
            for binding in ext.bindings:
                for item in binding.event_types:
                    if isinstance(item, EventType):
                        aggregated.register(item.event_type, name=item.name)
                        for alias in item.aliases:
                            aggregated.add_alias(item.event_type, alias)
                    else:
                        aggregated.register(item)
        aggregated.freeze()
        registry.add_provider(owning_module, object_(aggregated))

        if self._has_serializer and len(aggregated) == 0:
            warnings.warn(
                'A serializer is configured but no event types were registered via '
                'bind_aggregate(event_types=[...]). Deserialization will fail at runtime.',
                UserWarning,
                stacklevel=1,
            )


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
            for binding in ext.bindings:
                all_projection_types.extend(binding.projections)

        if all_projection_types:
            registry.add_provider(owning_module, many(IProjection, *all_projection_types))
        else:
            registry.add_provider(owning_module, object_((), provided_type=Sequence[IProjection]))
