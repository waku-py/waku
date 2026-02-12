from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest

from waku.cqrs.contracts.notification import INotification
from waku.eventsourcing.contracts.aggregate import EventSourcedAggregate
from waku.eventsourcing.modules import EventSourcingConfig, EventSourcingExtension, EventSourcingModule, EventType
from waku.eventsourcing.projection.interfaces import ICatchUpProjection, IProjection
from waku.eventsourcing.repository import EventSourcedRepository
from waku.eventsourcing.serialization.registry import EventTypeRegistry
from waku.eventsourcing.store.in_memory import InMemoryEventStore
from waku.eventsourcing.store.interfaces import IEventStore
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


def test_config_rejects_both_store_and_store_factory() -> None:
    class CustomStore(InMemoryEventStore):
        pass

    with pytest.raises(ValueError, match='Cannot set both store and store_factory'):
        EventSourcingConfig(store=CustomStore, store_factory=lambda: None)  # type: ignore[arg-type,return-value]


class SearchIndexProjection(ICatchUpProjection):
    projection_name = 'search_index'

    async def project(self, events: Sequence[StoredEvent], /) -> None:
        pass


class ItemListProjection(IProjection):
    projection_name = 'item_list'

    async def project(self, events: Sequence[StoredEvent], /) -> None:
        pass


async def test_catch_up_projections_registered_via_bind() -> None:
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
