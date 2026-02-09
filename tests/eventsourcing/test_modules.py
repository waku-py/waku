from __future__ import annotations

from dataclasses import dataclass

import pytest

from waku.cqrs.contracts.notification import INotification
from waku.eventsourcing.contracts.aggregate import EventSourcedAggregate
from waku.eventsourcing.modules import EventSourcingConfig, EventSourcingExtension, EventSourcingModule
from waku.eventsourcing.repository import EventSourcedRepository
from waku.eventsourcing.store.in_memory import InMemoryEventStore
from waku.eventsourcing.store.interfaces import IEventStore
from waku.modules import module
from waku.testing import create_test_app


@dataclass(frozen=True)
class ItemCreated(INotification):
    name: str


class Item(EventSourcedAggregate):
    def __init__(self) -> None:
        super().__init__()
        self.name: str = ''

    def create(self, name: str) -> None:
        self._raise_event(ItemCreated(name=name))

    def _apply(self, event: INotification) -> None:
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


def test_config_rejects_both_store_and_store_factory() -> None:
    class CustomStore(InMemoryEventStore):
        pass

    with pytest.raises(ValueError, match='Cannot set both store and store_factory'):
        EventSourcingConfig(store=CustomStore, store_factory=lambda: None)  # type: ignore[arg-type,return-value]
