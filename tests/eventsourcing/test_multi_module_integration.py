from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from waku.cqrs.contracts.notification import INotification
from waku.eventsourcing.contracts.aggregate import EventSourcedAggregate
from waku.eventsourcing.modules import EventSourcingExtension, EventSourcingModule, EventType
from waku.eventsourcing.projection.interfaces import IProjection
from waku.eventsourcing.repository import EventSourcedRepository
from waku.eventsourcing.serialization.registry import EventTypeRegistry
from waku.eventsourcing.upcasting import UpcasterChain, rename_field
from waku.modules import module
from waku.testing import create_test_app

if TYPE_CHECKING:
    from collections.abc import Sequence

    from waku.eventsourcing.contracts.event import StoredEvent


@dataclass(frozen=True)
class OrderPlaced(INotification):
    order_id: str
    customer_id: str
    total: int


@dataclass(frozen=True)
class OrderCancelled(INotification):
    order_id: str
    reason: str


SHARED_EVENTS: list[EventType] = [
    EventType(OrderPlaced, version=2, upcasters=[rename_field(from_version=1, old='amount', new='total')]),
    EventType(OrderCancelled),
]


class Order(EventSourcedAggregate):
    def __init__(self) -> None:
        super().__init__()
        self.order_id: str = ''
        self.customer_id: str = ''
        self.total: int = 0
        self.cancelled: bool = False

    def place(self, order_id: str, customer_id: str, total: int) -> None:
        self._raise_event(OrderPlaced(order_id=order_id, customer_id=customer_id, total=total))

    def cancel(self, reason: str) -> None:
        self._raise_event(OrderCancelled(order_id=self.order_id, reason=reason))

    def _apply(self, event: INotification) -> None:
        match event:
            case OrderPlaced(order_id=oid, customer_id=cid, total=t):
                self.order_id = oid
                self.customer_id = cid
                self.total = t
            case OrderCancelled():
                self.cancelled = True


class OrderRepository(EventSourcedRepository[Order]):
    pass


@dataclass
class AnalyticsState:
    total_orders: int = 0
    total_revenue: int = 0
    cancellations: int = 0
    orders: dict[str, int] = field(default_factory=dict)


class OrderAnalyticsProjection(IProjection):
    projection_name = 'order_analytics'

    def __init__(self) -> None:
        self.state = AnalyticsState()

    async def project(self, events: Sequence[StoredEvent], /) -> None:
        for event in events:
            match event.data:
                case OrderPlaced(order_id=oid, total=total):
                    self.state.total_orders += 1
                    self.state.total_revenue += total
                    self.state.orders[oid] = total
                case OrderCancelled(order_id=oid):
                    self.state.cancellations += 1
                    if oid in self.state.orders:
                        self.state.total_revenue -= self.state.orders.pop(oid)


class AnalyticsLog(EventSourcedAggregate):
    def __init__(self) -> None:
        super().__init__()
        self.event_count: int = 0

    def _apply(self, event: INotification) -> None:  # noqa: ARG002
        self.event_count += 1


class AnalyticsLogRepository(EventSourcedRepository[AnalyticsLog]):
    pass


def _create_modules() -> tuple[type, type]:
    orders_ext = EventSourcingExtension().bind_aggregate(
        repository=OrderRepository,
        event_types=SHARED_EVENTS,
    )
    analytics_ext = EventSourcingExtension().bind_aggregate(
        repository=AnalyticsLogRepository,
        event_types=SHARED_EVENTS,
        projections=[OrderAnalyticsProjection],
    )

    @module(imports=[EventSourcingModule.register()], extensions=[orders_ext])
    class OrdersModule:
        pass

    @module(extensions=[analytics_ext])
    class AnalyticsModule:
        pass

    return OrdersModule, AnalyticsModule


async def test_multi_module_app_boots_with_shared_events() -> None:
    orders, analytics = _create_modules()

    async with create_test_app(imports=[orders, analytics]) as app, app.container() as container:
        registry = await container.get(EventTypeRegistry)

        assert registry.resolve('OrderPlaced') is OrderPlaced
        assert registry.resolve('OrderCancelled') is OrderCancelled
        assert registry.get_version(OrderPlaced) == 2
        assert registry.get_version(OrderCancelled) == 1


async def test_shared_upcasters_registered_once() -> None:
    orders, analytics = _create_modules()

    async with create_test_app(imports=[orders, analytics]) as app, app.container() as container:
        chain = await container.get(UpcasterChain)

        result = chain.upcast('OrderPlaced', {'amount': 500, 'order_id': 'o1', 'customer_id': 'c1'}, schema_version=1)

        assert result == {'total': 500, 'order_id': 'o1', 'customer_id': 'c1'}


async def test_order_aggregate_round_trip() -> None:
    orders, analytics = _create_modules()

    async with create_test_app(imports=[orders, analytics]) as app, app.container() as container:
        repo = await container.get(OrderRepository)

        order = Order()
        order.place('order-1', 'customer-42', total=9900)
        await repo.save('order-1', order)

        loaded = await repo.load('order-1')

        assert loaded.order_id == 'order-1'
        assert loaded.customer_id == 'customer-42'
        assert loaded.total == 9900
        assert loaded.cancelled is False

        loaded.cancel('changed mind')
        await repo.save('order-1', loaded)

        reloaded = await repo.load('order-1')
        assert reloaded.cancelled is True
