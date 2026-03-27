from __future__ import annotations

import typing

from typing_extensions import get_original_bases

from waku.messaging.contracts.request import IRequest

if typing.TYPE_CHECKING:
    from waku.messaging.contracts.message import IMessage, ResponseT

__all__ = ['get_response_type']

_RESPONSE_T_DEFAULT: typing.Final[type[None]] = type(None)


def _is_request_origin(origin: type | None) -> bool:
    if origin is None:
        return False
    if origin is IRequest:
        return True
    return isinstance(origin, type) and issubclass(origin, IRequest)  # pyrefly: ignore[invalid-argument]


def _extract_response_from_bases(cls: type) -> type[ResponseT] | None:
    for base in get_original_bases(cls):
        origin = typing.get_origin(base)
        if not _is_request_origin(origin):
            if base is IRequest:
                return typing.cast('type[ResponseT]', _RESPONSE_T_DEFAULT)
            continue
        if args := typing.get_args(base):  # pragma: no branch
            response_type = args[0]
            if isinstance(response_type, typing.TypeVar):
                continue
            return typing.cast('type[ResponseT]', response_type)
    return None


def get_response_type(message_type: type[IMessage]) -> type[ResponseT]:
    """Extract response type from a message type.

    Returns ResponseT for IRequest[T] subclasses, type(None) for everything else.
    Walks the MRO -- always terminates at object.
    """
    for cls in message_type.__mro__:
        if cls is object:
            return typing.cast('type[ResponseT]', _RESPONSE_T_DEFAULT)
        if response_type := _extract_response_from_bases(cls):
            return response_type  # type: ignore[return-value]
    return typing.cast('type[ResponseT]', _RESPONSE_T_DEFAULT)  # pragma: no cover
