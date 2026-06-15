from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any, Protocol, Self, TypeAlias, runtime_checkable

from typing_extensions import override

from waku.di import Provider, WithParents, is_registered, many, object_, scoped
from waku.eventsourcing._introspection import resolve_generic_args
from waku.eventsourcing.contracts.aggregate import IDecider
from waku.eventsourcing.contracts.event import IMetadataEnricher
from waku.eventsourcing.decider.repository import DeciderRepository
from waku.eventsourcing.exceptions import (
    DuplicateAggregateNameError,
    EventSourcingConfigError,
    RegistryFrozenError,
    SnapshotMigrationChainError,
    UnknownEventTypeError,
    UpcasterChainError,
)
from waku.eventsourcing.forwarding import AppendedEventsCollector, ForwardingRegistry, IAppendedEvents
from waku.eventsourcing.projection.binding import CatchUpProjectionBinding
from waku.eventsourcing.projection.interfaces import ErrorPolicy, ICatchUpProjection, ICheckpointStore, IProjection
from waku.eventsourcing.projection.registry import CatchUpProjectionRegistry
from waku.eventsourcing.serialization.interfaces import IEventSerializer, ISnapshotStateSerializer
from waku.eventsourcing.serialization.json import JsonEventSerializer, JsonSnapshotStateSerializer
from waku.eventsourcing.serialization.registry import EventTypeRegistry
from waku.eventsourcing.snapshot.interfaces import ISnapshotStore
from waku.eventsourcing.snapshot.migration import SnapshotMigrationChain
from waku.eventsourcing.snapshot.registry import SnapshotConfig, SnapshotConfigRegistry
from waku.eventsourcing.store.interfaces import IEventStore
from waku.eventsourcing.upcasting.chain import UpcasterChain
from waku.extensions import AfterApplicationInit, OnModuleConfigure, OnModuleRegistration
from waku.messaging.exceptions import ImproperlyConfiguredError
from waku.modules import DynamicModule, ModuleMetadataRegistry, module
from waku.uow import IUnitOfWork

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping, Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

    from waku.application import WakuApplication
    from waku.eventsourcing.forwarding import ForwardDescriptor
    from waku.eventsourcing.repository import EventSourcedRepository
    from waku.eventsourcing.snapshot.interfaces import ISnapshotStrategy
    from waku.eventsourcing.snapshot.migration import ISnapshotMigration
    from waku.eventsourcing.upcasting.interfaces import IEventUpcaster
    from waku.messaging.contracts.event import IEvent
    from waku.modules import ModuleMetadata, ModuleType


__all__ = [
    'EventSourcingConfig',
    'EventSourcingExtension',
    'EventSourcingModule',
    'EventType',
    'EventTypeSpec',
    'SnapshotOptions',
]


@dataclass(frozen=True, slots=True)
class EventType:
    event_type: type[IEvent]
    name: str | None = field(default=None, kw_only=True)
    aliases: Sequence[str] = field(default=(), kw_only=True)
    version: int = field(default=1, kw_only=True)
    upcasters: Sequence[IEventUpcaster] = field(default=(), kw_only=True)


EventTypeSpec: TypeAlias = 'type[IEvent] | EventType'


@dataclass(frozen=True, slots=True, kw_only=True)
class SnapshotOptions:
    strategy: ISnapshotStrategy
    schema_version: int = 1
    migrations: Sequence[ISnapshotMigration] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class AggregateBinding:
    repository: type[EventSourcedRepository[Any]]
    event_types: Sequence[EventTypeSpec]
    projections: Sequence[type[IProjection]]
    snapshot: SnapshotOptions | None


@dataclass(frozen=True, slots=True, kw_only=True)
class DeciderBinding:
    repository: type[DeciderRepository[Any, Any, Any]]
    decider: type[IDecider[Any, Any, Any]]
    event_types: Sequence[EventTypeSpec]
    projections: Sequence[type[IProjection]]
    snapshot: SnapshotOptions | None


@dataclass(frozen=True, slots=True, kw_only=True)
class EventSourcingConfig:
    store: type[IEventStore] | Callable[..., IEventStore]
    event_serializer: type[IEventSerializer] | Callable[..., IEventSerializer] | None = None
    snapshot_store: type[ISnapshotStore] | Callable[..., ISnapshotStore] | None = None
    snapshot_state_serializer: type[ISnapshotStateSerializer] | Callable[..., ISnapshotStateSerializer] | None = None
    checkpoint_store: type[ICheckpointStore] | Callable[..., ICheckpointStore] | None = None
    enrichers: Sequence[type[IMetadataEnricher]] = ()
    forwarding: Sequence[ForwardDescriptor] = ()


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


@runtime_checkable
class _SupportsSession(Protocol):
    @property
    def session(self) -> AsyncSession: ...


class _StoreSessionIdentityExtension(AfterApplicationInit):
    """Fail fast when the event store and the UoW do not share one AsyncSession.

    Atomic append+forward holds only if the event-store append and the UoW commit run on the SAME
    session within a request scope; a mis-wire that hands the store a different AsyncSession silently
    splits the append from the commit/outbox. This turns that silent split into a startup error.

    Session identity is sqla-specific — only ``SqlAlchemyEventStore`` / ``SqlAlchemyUnitOfWork`` expose
    a ``session``. Stores/UoWs that do not (e.g. ``InMemoryEventStore``, a non-sqla UoW) cannot prove
    identity and are skipped (documented no-op); the messaging presence check still applies to them.

    Unlike the messaging presence checks (which only call ``is_registered``), this extension must
    ``get`` the store and UoW to compare their session identity — it intentionally pays that boot-time
    construction cost. Keep the ``get`` calls; do not "harmonize" them to a presence-only check.
    """

    __slots__ = ()

    @override
    async def after_app_init(self, app: WakuApplication) -> None:
        # Check + resolve inside a request scope: IUnitOfWork is typically scoped (one session per
        # request) and is not resolvable at app scope. No UoW configured -> nothing to validate.
        async with app.container() as scope:
            if not await is_registered(scope, IUnitOfWork):
                return
            store = await scope.get(IEventStore)
            uow = await scope.get(IUnitOfWork)
            if not (isinstance(store, _SupportsSession) and isinstance(uow, _SupportsSession)):
                return
            if store.session is not uow.session:
                msg = (
                    'Event store and IUnitOfWork must share one AsyncSession for atomic append+forward, '
                    'but they resolved to different sessions. Register a single scoped(AsyncSession) used '
                    'by both (e.g. waku.messaging.sqla.shared_session).'
                )
                raise ImproperlyConfiguredError(msg)


@module()
class EventSourcingModule:
    @classmethod
    def register(cls, config: EventSourcingConfig, /) -> DynamicModule:
        providers: list[Provider] = [
            scoped(IEventStore, config.store),
            # Scoped per command: the event store records appended events into it, EventForwardingBehavior
            # drains them. Always registered (cheap holder) — the store factory injects it by keyword.
            scoped(IAppendedEvents, AppendedEventsCollector),
            # Opt-in internal->integration translation rules consumed by EventForwardingBehavior.
            object_(ForwardingRegistry(config.forwarding), provided_type=ForwardingRegistry),
        ]

        if config.event_serializer is not None:
            providers.append(scoped(IEventSerializer, config.event_serializer))
        else:
            providers.append(scoped(IEventSerializer, JsonEventSerializer))

        if config.snapshot_store is not None:
            providers.append(scoped(ISnapshotStore, config.snapshot_store))

        if config.snapshot_state_serializer is not None:
            providers.append(scoped(ISnapshotStateSerializer, config.snapshot_state_serializer))
        else:
            providers.append(scoped(ISnapshotStateSerializer, JsonSnapshotStateSerializer))

        if config.checkpoint_store is not None:
            providers.append(scoped(ICheckpointStore, config.checkpoint_store))

        providers.append(many(IMetadataEnricher, *config.enrichers))

        return DynamicModule(
            parent_module=cls,
            providers=providers,
            extensions=[
                EventSourcingRegistryAggregator(has_serializer=config.event_serializer is not None),
                _StoreSessionIdentityExtension(),
            ],
            is_global=True,
        )


@dataclass
class EventSourcingExtension(OnModuleConfigure):
    _bindings: list[AggregateBinding] = field(default_factory=list, init=False)
    _decider_bindings: list[DeciderBinding] = field(default_factory=list, init=False)
    _catch_up_bindings: list[CatchUpProjectionBinding] = field(default_factory=list, init=False)
    _registry: EventSourcingRegistry = field(default_factory=EventSourcingRegistry, init=False)

    def bind_aggregate(
        self,
        repository: type[EventSourcedRepository[Any]],
        event_types: Sequence[EventTypeSpec] = (),
        projections: Sequence[type[IProjection]] = (),
        snapshot: SnapshotOptions | None = None,
    ) -> Self:
        self._bindings.append(
            AggregateBinding(
                repository=repository,
                event_types=event_types,
                projections=projections,
                snapshot=snapshot,
            )
        )
        self._registry.projection_types.extend(projections)
        self._registry.event_type_bindings.extend(event_types)
        return self

    def bind_decider(
        self,
        repository: type[DeciderRepository[Any, Any, Any]],
        decider: type[IDecider[Any, Any, Any]],
        event_types: Sequence[EventTypeSpec] = (),
        projections: Sequence[type[IProjection]] = (),
        snapshot: SnapshotOptions | None = None,
    ) -> Self:
        self._decider_bindings.append(
            DeciderBinding(
                repository=repository,
                decider=decider,
                event_types=event_types,
                projections=projections,
                snapshot=snapshot,
            )
        )
        self._registry.projection_types.extend(projections)
        self._registry.event_type_bindings.extend(event_types)
        return self

    def bind_catch_up_projection(  # noqa: PLR0913
        self,
        projection: type[ICatchUpProjection],
        *,
        error_policy: ErrorPolicy = ErrorPolicy.STOP,
        max_retry_attempts: int = 0,
        base_retry_delay_seconds: float = 10.0,
        max_retry_delay_seconds: float = 300.0,
        batch_size: int = 100,
        gap_detection_enabled: bool = False,
        gap_timeout_seconds: float = 10.0,
    ) -> Self:
        self._registry.catch_up_projection_types.append(projection)
        self._catch_up_bindings.append(
            CatchUpProjectionBinding(
                projection=projection,
                error_policy=error_policy,
                max_retry_attempts=max_retry_attempts,
                base_retry_delay_seconds=base_retry_delay_seconds,
                max_retry_delay_seconds=max_retry_delay_seconds,
                batch_size=batch_size,
                gap_detection_enabled=gap_detection_enabled,
                gap_timeout_seconds=gap_timeout_seconds,
            )
        )
        return self

    @property
    def catch_up_bindings(self) -> list[CatchUpProjectionBinding]:
        return self._catch_up_bindings

    @property
    def registry(self) -> EventSourcingRegistry:
        return self._registry

    def aggregate_names(self) -> Iterator[tuple[str, type]]:
        for binding in self._bindings:
            yield binding.repository.aggregate_name, binding.repository
        for binding in self._decider_bindings:
            yield binding.repository.aggregate_name, binding.repository

    def snapshot_bindings(self) -> Iterator[tuple[str, SnapshotOptions]]:
        for binding in self._bindings:
            if binding.snapshot is not None:
                yield binding.repository.aggregate_name, binding.snapshot
        for binding in self._decider_bindings:
            if binding.snapshot is not None:
                yield binding.repository.aggregate_name, binding.snapshot

    def on_module_configure(self, metadata: ModuleMetadata) -> None:
        for binding in self._bindings:
            repo_type = binding.repository
            metadata.providers.append(scoped(WithParents[repo_type], repo_type))  # type: ignore[misc,valid-type]

        for binding in self._decider_bindings:
            repo_type = binding.repository
            metadata.providers.append(scoped(WithParents[repo_type], repo_type))  # type: ignore[misc,valid-type]
            decider_iface = self._resolve_decider_interface(repo_type)
            metadata.providers.append(scoped(decider_iface, binding.decider))

    @staticmethod
    def _resolve_decider_interface(repo_type: type[Any]) -> type[Any]:
        args = resolve_generic_args(repo_type, DeciderRepository)
        if args and len(args) == 3:  # noqa: PLR2004
            return IDecider[args[0], args[1], args[2]]  # type: ignore[valid-type]
        return IDecider  # pragma: no cover


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
        all_aggregate_names: defaultdict[str, list[str]] = defaultdict(list)
        all_catch_up_bindings: list[CatchUpProjectionBinding] = []

        for module_type, ext in registry.find_extensions(EventSourcingExtension):
            aggregated.merge(ext.registry)
            all_catch_up_bindings.extend(ext.catch_up_bindings)
            for provider in ext.registry.handler_providers():
                registry.add_provider(module_type, provider)
            for name, repo_type in ext.aggregate_names():
                all_aggregate_names[name].append(repo_type.__qualname__)

        for name, repo_names in all_aggregate_names.items():
            if len(repo_names) > 1:
                raise DuplicateAggregateNameError(name, repo_names)

        for provider in aggregated.collector_providers():
            registry.add_provider(owning_module, provider)

        event_type_registry, upcaster_chain = self._build_type_registry(aggregated)
        registry.add_provider(owning_module, object_(event_type_registry))
        registry.add_provider(owning_module, object_(upcaster_chain))

        resolved_bindings = self._resolve_catch_up_bindings(all_catch_up_bindings, event_type_registry)
        registry.add_provider(
            owning_module,
            object_(CatchUpProjectionRegistry(resolved_bindings)),
        )

        aggregated.freeze()
        registry.add_provider(owning_module, object_(aggregated))

        snapshot_config_registry = self._build_snapshot_config_registry(
            registry.find_extensions(EventSourcingExtension),
        )
        registry.add_provider(owning_module, object_(snapshot_config_registry))

        if self._has_serializer and len(event_type_registry) == 0:
            msg = (
                'A serializer is configured but no event types were registered via '
                'bind_aggregate(event_types=[...]). Deserialization will fail at runtime.'
            )
            raise EventSourcingConfigError(msg)

    @staticmethod
    def _resolve_catch_up_bindings(
        bindings: Sequence[CatchUpProjectionBinding],
        event_type_registry: EventTypeRegistry,
    ) -> tuple[CatchUpProjectionBinding, ...]:
        resolved: list[CatchUpProjectionBinding] = []
        for binding in bindings:
            if binding.projection.event_types is None:
                resolved.append(binding)
                continue
            names: list[str] = []
            for et in binding.projection.event_types:
                try:
                    names.append(event_type_registry.get_name(et))
                except UnknownEventTypeError:
                    msg = (
                        f'Projection {binding.projection.__name__!r} declares event type '
                        f'{et.__name__!r} in its event_types, but it is not registered '
                        f'via bind_aggregate(event_types=[...]).'
                    )
                    raise EventSourcingConfigError(msg) from None
            resolved.append(replace(binding, event_type_names=tuple(names)))
        return tuple(resolved)

    @staticmethod
    def _build_snapshot_config_registry(
        extensions: Iterator[tuple[ModuleType, EventSourcingExtension]],
    ) -> SnapshotConfigRegistry:
        configs: dict[str, SnapshotConfig] = {}
        for _module_type, ext in extensions:
            for aggregate_name, options in ext.snapshot_bindings():
                migration_chain = SnapshotMigrationChain(options.migrations)
                _validate_snapshot_migration_target(aggregate_name, options.schema_version, migration_chain)
                configs[aggregate_name] = SnapshotConfig(
                    strategy=options.strategy,
                    schema_version=options.schema_version,
                    migration_chain=migration_chain,
                )
        return SnapshotConfigRegistry(configs)

    @classmethod
    def _build_type_registry(cls, aggregated: EventSourcingRegistry) -> tuple[EventTypeRegistry, UpcasterChain]:
        registry = EventTypeRegistry()
        upcasters: dict[str, Sequence[IEventUpcaster]] = {}

        for spec in cls._deduplicate(aggregated.event_type_bindings):
            item = spec if isinstance(spec, EventType) else EventType(spec)

            registry.register(item.event_type, name=item.name, version=item.version)
            for alias in item.aliases:
                registry.add_alias(item.event_type, alias)

            if item.upcasters:
                type_name = item.name or item.event_type.__name__
                cls._validate_upcaster_versions(item.upcasters, type_name, item.version)
                if type_name in upcasters and upcasters[type_name] is not item.upcasters:
                    msg = f'Conflicting upcaster definitions for event type {type_name!r}'
                    raise UpcasterChainError(msg)
                upcasters[type_name] = item.upcasters

        registry.freeze()
        return registry, UpcasterChain({k: list(v) for k, v in upcasters.items()})

    @staticmethod
    def _deduplicate(bindings: Sequence[EventTypeSpec]) -> Iterator[EventTypeSpec]:
        seen: set[int] = set()
        for item in bindings:
            item_id = id(item)
            if item_id not in seen:
                seen.add(item_id)
                yield item

    @staticmethod
    def _validate_upcaster_versions(upcasters: Sequence[IEventUpcaster], type_name: str, version: int) -> None:
        for u in upcasters:
            if u.from_version >= version:
                msg = (
                    f'Upcaster from_version {u.from_version} for event type {type_name!r} '
                    f'must be < event version {version}'
                )
                raise UpcasterChainError(msg)


def _validate_snapshot_migration_target(
    aggregate_name: str,
    schema_version: int,
    chain: SnapshotMigrationChain,
) -> None:
    if not chain.migrations:
        if schema_version != 1:
            msg = (
                f'Snapshot config for aggregate {aggregate_name!r}: '
                f'schema_version is {schema_version} but no migrations are provided. '
                f'Either set schema_version=1 or provide migrations.'
            )
            raise SnapshotMigrationChainError(msg)
        return

    first_from_version = chain.migrations[0].from_version
    if first_from_version != 1:
        msg = (
            f'Snapshot config for aggregate {aggregate_name!r}: '
            f'migration chain starts at version {first_from_version} but must start at version 1.'
        )
        raise SnapshotMigrationChainError(msg)

    final_to_version = chain.migrations[-1].to_version
    if final_to_version != schema_version:
        msg = (
            f'Snapshot config for aggregate {aggregate_name!r}: '
            f'migration chain reaches version {final_to_version} but schema_version is {schema_version}. '
            f'The final migration to_version must equal schema_version.'
        )
        raise SnapshotMigrationChainError(msg)
