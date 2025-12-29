from __future__ import annotations

import functools
import typing

from typing_extensions import get_original_bases

from waku.cqrs.contracts.request import IRequest

if typing.TYPE_CHECKING:
    from waku.cqrs.contracts import ResponseT

__all__ = ['get_request_response_type']


def _is_request_origin(origin: type | None) -> bool:
    if origin is None:
        return False
    if origin is IRequest:
        return True
    try:
        return isinstance(origin, type) and issubclass(origin, IRequest)  # pyrefly: ignore[invalid-argument]
    except TypeError:
        return False


def _extract_response_from_bases(cls: type) -> type[ResponseT] | None:
    for base in get_original_bases(cls):
        if not _is_request_origin(typing.get_origin(base)):
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
    supporting direct implementations, Request subclasses, and nested inheritance.

    Raises:
        TypeError: if response type cannot be extracted from the request type.
    """
    for cls in request_type.__mro__:
        if cls is object:
            break
        if response_type := _extract_response_from_bases(cls):
            return response_type  # type: ignore[return-value]

    msg = f'Could not extract response type from {request_type.__name__}'
    raise TypeError(msg)
