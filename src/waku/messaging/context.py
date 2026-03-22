from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping
    from uuid import UUID


@dataclass(frozen=True, slots=True, kw_only=True)
class MessageContext:
    correlation_id: UUID
    causation_id: UUID
    message_id: UUID
    headers: Mapping[str, str]


_message_context: ContextVar[MessageContext | None] = ContextVar('_message_context', default=None)


def get_message_context() -> MessageContext:
    ctx = _message_context.get()
    if ctx is None:
        msg = 'No active message context. This function must be called within a MessageBus operation.'
        raise RuntimeError(msg)
    return ctx


def try_get_message_context() -> MessageContext | None:
    return _message_context.get()


def set_message_context(ctx: MessageContext) -> Token[MessageContext | None]:
    return _message_context.set(ctx)


def reset_message_context(token: Token[MessageContext | None]) -> None:
    _message_context.reset(token)
