from __future__ import annotations

import functools
import typing

from typing_extensions import get_original_bases

from waku.cqrs.contracts.request import ResponseT

if typing.TYPE_CHECKING:
    from waku.cqrs.contracts.request import Request

__all__ = ['get_request_response_type']


@functools.cache
def get_request_response_type(request_type: type[Request[ResponseT]]) -> type[ResponseT]:
    return typing.cast(type[ResponseT], typing.get_args(get_original_bases(request_type)[0])[0])
