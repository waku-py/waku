from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Self, TypeAlias

from typing_extensions import override

from waku.di import Provider, WithParents, many, object_, scoped
from waku.eventsourcing.contracts.event import IMetadataEnricher
from waku.eventsourcing.exceptions import RegistryFrozenError
from waku.eventsourcing.projection.interfaces import ICatchUpProjection, ICheckpointStore, IProjection
from waku.eventsourcing.serialization.interfaces import IEventSerializer
from waku.eventsourcing.serialization.registry import EventTypeRegistry
from waku.eventsourcing.snapshot.interfaces import ISnapshotStore, ISnapshotStrategy
from waku.eventsourcing.snapshot.serialization import ISnapshotStateSerializer
from waku.eventsourcing.store.in_memory import InMemoryEventStore
from waku.eventsourcing.store.interfaces import IEventStore
from waku.extensions import OnModuleConfigure, OnModuleRegistration
from waku.modules import DynamicModule, ModuleMetadataRegistry, module

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping, Sequence

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
    checkpoint_store: type[ICheckpointStore] | None = None
    store_factory: Callable[..., IEventStore] | None = None
    snapshot_store_factory: Callable[..., ISnapshotStore] | None = None
    checkpoint_store_factory: Callable[..., ICheckpointStore] | None = None
    enrichers: Sequence[type[IMetadataEnricher]] = ()

    def __post_init__(self) -> None:
        if self.store is not None and self.store_factory is not None:
            msg = 'Cannot set both store and store_factory'
            raise ValueError(msg)
        if self.snapshot_store is not None and self.snapshot_store_factory is not None:
            msg = 'Cannot set both snapshot_store and snapshot_store_factory'
            raise ValueError(msg)
        if self.checkpoint_store is not None and self.checkpoint_store_factory is not None:
            msg = 'Cannot set both checkpoint_store and checkpoint_store_factory'
            raise ValueError(msg)


@dataclass(slots=True)
class EventSourcingRegistry:
    projection_types: list[type[IProjection]] = field(default_factory=list)
    catch_up_projection_types: list[type[ICatchUpProjection]] = field(default_factory=list)
    event_type_bindings: list[EventTypeSpec] = field(default_factory=list)
    _frozen: bool = field(default=False, init=False, repr=False)

    def merge(self, other: EventSourcingRegistry) -> None:
        self._check_not_frozen()
        self.projection_types.extend(other.projection_types)
        self.catch_up_projection_types.extend(other.catch_up_projection_types)
        self.event_type_bindings.extend(other.event_type_bindings)

    def freeze(self) -> None:
        self._frozen = True

    def handler_providers(self) -> Iterator[Provider]:
        if self.projection_types:
            yield many(IProjection, *self.projection_types, collect=False)
        if self.catch_up_projection_types:
            yield many(ICatchUpProjection, *self.catch_up_projection_types, collect=False)

    @staticmethod
    def collector_providers() -> Iterator[Provider]:
        yield many(IProjection, collect=True)
        yield many(ICatchUpProjection, collect=True)

    def _check_not_frozen(self) -> None:
        if self._frozen:
            raise RegistryFrozenError


@module()
class EventSourcingModule:
    @classmethod
    def register(cls, config: EventSourcingConfig | None = None, /) -> DynamicModule:
        config_ = config or EventSourcingConfig()
        providers: list[Provider] = []

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

        if config_.checkpoint_store_factory is not None:
            providers.append(scoped(ICheckpointStore, config_.checkpoint_store_factory))
        elif config_.checkpoint_store is not None:
            providers.append(scoped(ICheckpointStore, config_.checkpoint_store))

        providers.append(many(IMetadataEnricher, *config_.enrichers))

        return DynamicModule(
            parent_module=cls,
            providers=providers,
            extensions=[
                EventSourcingRegistryAggregator(has_serializer=config_.event_serializer is not None),
            ],
            is_global=True,
        )


@dataclass
class EventSourcingExtension(OnModuleConfigure):
    _bindings: list[AggregateBinding] = field(default_factory=list, init=False)
    _registry: EventSourcingRegistry = field(default_factory=EventSourcingRegistry, init=False)

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
        self._registry.projection_types.extend(projections)
        self._registry.event_type_bindings.extend(event_types)
        return self

    def bind_catch_up_projections(self, projections: Sequence[type[ICatchUpProjection]]) -> Self:
        self._registry.catch_up_projection_types.extend(projections)
        return self

    @property
    def registry(self) -> EventSourcingRegistry:
        return self._registry

    def on_module_configure(self, metadata: ModuleMetadata) -> None:
        for binding in self._bindings:
            repo_type = binding.repository
            metadata.providers.append(scoped(WithParents[repo_type], repo_type))  # type: ignore[misc,valid-type]
            if binding.snapshot_strategy is not None:
                metadata.providers.append(object_(binding.snapshot_strategy, provided_type=ISnapshotStrategy))


class EventSourcingRegistryAggregator(OnModuleRegistration):
    def __init__(self, *, has_serializer: bool = False) -> None:
        self._has_serializer = has_serializer

    @override
    def on_module_registration(
        self,
        registry: ModuleMetadataRegistry,
        owning_module: ModuleType,
        context: Mapping[Any, Any] | None,
    ) -> None:
        aggregated = EventSourcingRegistry()

        for module_type, ext in registry.find_extensions(EventSourcingExtension):
            aggregated.merge(ext.registry)
            for provider in ext.registry.handler_providers():
                registry.add_provider(module_type, provider)

        for provider in aggregated.collector_providers():
            registry.add_provider(owning_module, provider)

        aggregated.freeze()
        registry.add_provider(owning_module, object_(aggregated))

        event_type_registry = EventTypeRegistry()
        for item in aggregated.event_type_bindings:
            if isinstance(item, EventType):
                event_type_registry.register(item.event_type, name=item.name)
                for alias in item.aliases:
                    event_type_registry.add_alias(item.event_type, alias)
            else:
                event_type_registry.register(item)
        event_type_registry.freeze()
        registry.add_provider(owning_module, object_(event_type_registry))

        if self._has_serializer and len(event_type_registry) == 0:
            warnings.warn(
                'A serializer is configured but no event types were registered via '
                'bind_aggregate(event_types=[...]). Deserialization will fail at runtime.',
                UserWarning,
                stacklevel=1,
            )
