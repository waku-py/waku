from __future__ import annotations

from typing_extensions import TypeVar

from waku.messages import IMessage

__all__ = [
    'MessageT',
    'ResponseT',
]

MessageT = TypeVar('MessageT', bound=IMessage, contravariant=True)  # noqa: PLC0105
ResponseT = TypeVar('ResponseT', default=None, covariant=True)  # noqa: PLC0105
