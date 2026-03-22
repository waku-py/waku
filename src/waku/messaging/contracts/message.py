from __future__ import annotations

from typing_extensions import TypeVar

__all__ = [
    'IMessage',
    'MessageT',
    'ResponseT',
]


class IMessage:
    __slots__ = ()


MessageT = TypeVar('MessageT', bound=IMessage, contravariant=True)  # noqa: PLC0105
ResponseT = TypeVar('ResponseT', default=None, covariant=True)  # noqa: PLC0105
