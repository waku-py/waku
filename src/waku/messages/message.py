from __future__ import annotations

from typing import ClassVar

# runtime import (not TYPE_CHECKING) so the message_identity ClassVar annotation stays resolvable
# under get_type_hints introspection (the wire identity itself is read from cls.__dict__)
from waku.messages.identity import MessageIdentity  # noqa: TC001

__all__ = [
    'IMessage',
]


class IMessage:
    __slots__ = ()
    # opt-in wire name; own-class only, does not inherit (see resolve_message_identity)
    message_identity: ClassVar[str | MessageIdentity]
