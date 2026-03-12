from __future__ import annotations

import functools
import typing

from typing_extensions import get_original_bases

from waku.messaging.contracts.request import IRequest

if typing.TYPE_CHECKING:
    from waku.messaging.contracts import ResponseT

__all__ = ['get_request_response_type']

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
        if args := typing.get_args(base):
            response_type = args[0]
            if isinstance(response_type, typing.TypeVar):
                continue
            return typing.cast('type[ResponseT]', response_type)
    return None


@functools.cache
def get_request_response_type(request_type: type[IRequest[ResponseT]]) -> type[ResponseT]:
    """Extract the response type from an IRequest implementation.

    Searches through the class hierarchy to find IRequest or its subclasses,
    supporting direct implementations and nested inheritance.
    Falls back to the TypeVar default when no explicit type argument is provided.

    Raises:
        TypeError: if response type cannot be extracted from the request type.
    """
    for cls in request_type.__mro__:
        if cls is object:
            msg = f'Could not extract response type from {request_type.__name__}'
            raise TypeError(msg)
        if response_type := _extract_response_from_bases(cls):
            return response_type  # type: ignore[return-value]

    msg = f'Could not extract response type from {request_type.__name__}'  # pragma: no cover
    raise TypeError(msg)  # pragma: no cover
