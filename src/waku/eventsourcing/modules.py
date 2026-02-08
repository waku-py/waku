from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Self

from waku.di import WithParents, scoped
from waku.eventsourcing.store.in_memory import InMemoryEventStore
from waku.eventsourcing.store.interfaces import IEventStore
from waku.extensions import OnModuleConfigure
from waku.modules import DynamicModule, module

if TYPE_CHECKING:
    from waku.eventsourcing.repository import EventSourcedRepository
    from waku.modules import ModuleMetadata

__all__ = [
    'EventSourcingConfig',
    'EventSourcingExtension',
    'EventSourcingModule',
]


@dataclass(frozen=True, slots=True, kw_only=True)
class EventSourcingConfig:
    event_store_type: type[IEventStore] = InMemoryEventStore


@module()
class EventSourcingModule:
    @classmethod
    def register(cls, config: EventSourcingConfig | None = None, /) -> DynamicModule:
        config_ = config or EventSourcingConfig()
        return DynamicModule(
            parent_module=cls,
            providers=[
                scoped(WithParents[IEventStore], config_.event_store_type),  # ty:ignore[not-subscriptable]
            ],
            is_global=True,
        )


class EventSourcingExtension(OnModuleConfigure):
    def __init__(self) -> None:
        self._repository_types: list[tuple[type[EventSourcedRepository[Any]], type[EventSourcedRepository[Any]]]] = []

    def bind_repository(self, repository_type: type[EventSourcedRepository[Any]]) -> Self:
        self._repository_types.append((repository_type, repository_type))
        return self

    def on_module_configure(self, metadata: ModuleMetadata) -> None:
        for base_type, impl_type in self._repository_types:
            metadata.providers.append(scoped(base_type, impl_type))
