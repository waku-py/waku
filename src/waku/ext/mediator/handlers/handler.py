from __future__ import annotations

import abc
import uuid
from dataclasses import dataclass, field
from typing import Any, Generic, TypeAlias, TypeVar

__all__ = [
    'HandlerType',
    'Request',
    'RequestHandler',
    'RequestT',
    'Response',
    'ResponseT',
]

RequestT = TypeVar('RequestT', bound='Request[Any]', contravariant=True)  # noqa: PLC0105
ResponseT = TypeVar('ResponseT', bound='Response | None', covariant=True)  # noqa: PLC0105


@dataclass(frozen=True, kw_only=True)
class Request(Generic[ResponseT]):
    """Base class for request-type objects."""

    request_id: uuid.UUID = field(default_factory=uuid.uuid4)


@dataclass(frozen=True, kw_only=True)
class Response:
    """Base class for response type objects."""


class RequestHandler(abc.ABC, Generic[RequestT, ResponseT]):
    @abc.abstractmethod
    async def handle(self, request: RequestT) -> ResponseT:
        raise NotImplementedError


HandlerType: TypeAlias = type[RequestHandler[RequestT, ResponseT]]
