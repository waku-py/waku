from __future__ import annotations

from typing import Protocol, TypeVar, runtime_checkable

__all__ = [
    'INotification',
    'NotificationT',
]


@runtime_checkable
class INotification(Protocol):
    """Marker interface for notification-type objects (events).

    This is a pure marker protocol with no required attributes or methods.
    Implement this protocol for domain events that need custom identification
    strategies or no identification at all.

    MediatR equivalent: INotification

    Example::

        @dataclass(frozen=True)
        class OrderPlaced(INotification):
            order_id: str
            customer_id: str


        # Or with custom identification:
        @dataclass(frozen=True)
        class DomainEvent(INotification):
            aggregate_id: str
            occurred_at: datetime


        @dataclass(frozen=True)
        class OrderPlaced(DomainEvent):
            order_id: str

    """

    __slots__ = ()


NotificationT = TypeVar('NotificationT', bound=INotification, contravariant=True)  # noqa: PLC0105
