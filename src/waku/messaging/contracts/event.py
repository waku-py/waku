from __future__ import annotations

from typing import Protocol, TypeVar, runtime_checkable

__all__ = [
    'EventT',
    'IEvent',
]


@runtime_checkable
class IEvent(Protocol):
    """Marker interface for event-type objects.

    This is a pure marker protocol with no required attributes or methods.
    Implement this protocol for domain events.

    Example::

        @dataclass(frozen=True)
        class OrderPlaced(IEvent):
            order_id: str
            customer_id: str

    """

    __slots__ = ()


EventT = TypeVar('EventT', bound=IEvent, contravariant=True)  # noqa: PLC0105
