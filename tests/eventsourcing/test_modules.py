from __future__ import annotations

from dataclasses import dataclass

from typing_extensions import override

from waku.cqrs.contracts.notification import INotification
from waku.eventsourcing.contracts.aggregate import EventSourcedAggregate
from waku.eventsourcing.contracts.stream import StreamId
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
    aggregate_type_name = 'Item'

    @override
    def create_aggregate(self) -> Item:
        return Item()

    @override
    def _stream_id(self, aggregate_id: str) -> StreamId:
        return StreamId.for_aggregate('Item', aggregate_id)


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
    config = EventSourcingConfig(event_store_type=InMemoryEventStore)
    async with (
        create_test_app(
            imports=[EventSourcingModule.register(config)],
        ) as app,
        app.container() as container,
    ):
        store = await container.get(IEventStore)
        assert isinstance(store, InMemoryEventStore)


async def test_event_sourcing_extension_binds_repository() -> None:
    es_ext = EventSourcingExtension().bind_repository(ItemRepository)

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
