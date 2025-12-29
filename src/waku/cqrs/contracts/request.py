from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Generic, Protocol, runtime_checkable

from typing_extensions import TypeVar

__all__ = [
    'IRequest',
    'Request',
    'RequestT',
    'Response',
    'ResponseT',
]


ResponseT = TypeVar('ResponseT', bound='Response | None', default=None, covariant=True)  # noqa: PLC0105


@runtime_checkable
class IRequest(Protocol[ResponseT]):
    """Marker interface for request-type objects (commands/queries).

    This is a pure marker protocol with no required attributes or methods.
    Implement this protocol for requests that need custom identification
    strategies or no identification at all.

    MediatR equivalent: IRequest<TResponse>

    Example::

        @dataclass(frozen=True)
        class GetUserQuery(IRequest[UserDTO]):
            user_id: str


        @dataclass(frozen=True)
        class CreateOrderCommand(IRequest[OrderId]):
            customer_id: str
            items: list[OrderItem]

    """

    __slots__ = ()


RequestT = TypeVar('RequestT', bound=IRequest[Any], contravariant=True)  # noqa: PLC0105


@dataclass(frozen=True, kw_only=True)
class Request(IRequest[ResponseT], Generic[ResponseT]):
    """Convenience base class for requests with auto-generated ID.

    Use this class when you want automatic request_id generation.
    For custom identification strategies, implement IRequest directly.

    Example::

        @dataclass(frozen=True, kw_only=True)
        class GetUserQuery(Request[UserDTO]):
            user_id: str

    """

    request_id: uuid.UUID = field(default_factory=uuid.uuid4)


@dataclass(frozen=True, kw_only=True)
class Response:
    """Base class for response type objects."""
