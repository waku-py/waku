from __future__ import annotations

import functools
import typing

from waku.mediator.contracts.request import ResponseT

if typing.TYPE_CHECKING:
    from waku.mediator.contracts.request import Request

__all__ = ['get_request_response_type']


@functools.cache
def get_request_response_type(request_type: type[Request[ResponseT]]) -> type[ResponseT]:
    return typing.cast(type[ResponseT], typing.get_args(request_type.__orig_bases__[0])[0])  # type: ignore[attr-defined]
