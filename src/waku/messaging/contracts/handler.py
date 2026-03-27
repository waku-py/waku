from __future__ import annotations

from typing import TYPE_CHECKING, Any, TypeAlias

if TYPE_CHECKING:
    from waku.messaging.handler import MessageHandler

__all__ = ['HandlerType']

HandlerType: TypeAlias = 'type[MessageHandler[Any, Any]]'
