from __future__ import annotations

from typing import ClassVar

from typing_extensions import TypeVar

# runtime-needed (not TYPE_CHECKING) so get_type_hints can resolve the ClassVar
from waku.messaging.contracts.identity import MessageIdentity  # noqa: TC001

__all__ = [
    'IMessage',
    'MessageT',
    'ResponseT',
]


class IMessage:
    __slots__ = ()
    # opt-in wire name; own-class only, does not inherit (see resolve_message_identity)
    message_identity: ClassVar[str | MessageIdentity]


MessageT = TypeVar('MessageT', bound=IMessage, contravariant=True)  # noqa: PLC0105
ResponseT = TypeVar('ResponseT', default=None, covariant=True)  # noqa: PLC0105
