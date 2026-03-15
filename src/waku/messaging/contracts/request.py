from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from typing_extensions import TypeVar

from waku.messaging.contracts.message import IMessage, ResponseT

__all__ = [
    'IRequest',
    'RequestT',
]


@runtime_checkable
class IRequest(IMessage, Protocol[ResponseT]):
    """Marker interface for request-type objects (commands/queries).

    Example::

        @dataclass(frozen=True)
        class GetUserQuery(IRequest[UserDTO]):
            user_id: str


        @dataclass(frozen=True)
        class CreateOrderCommand(IRequest):  # void command, returns None
            order_id: str

    """

    __slots__ = ()


RequestT = TypeVar('RequestT', bound=IRequest[Any], contravariant=True)  # noqa: PLC0105
