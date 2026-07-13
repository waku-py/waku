from __future__ import annotations

import contextlib
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Generator, Mapping
    from uuid import UUID

    from waku.messaging.contracts.envelope import MessageEnvelope

__all__ = [
    'MessageContext',
    'get_message_context',
    'try_get_message_context',
]


@dataclass(frozen=True, slots=True, kw_only=True)
class MessageContext:
    correlation_id: str
    causation_id: str
    message_id: UUID
    headers: Mapping[str, str]
    group_id: str | None = None
    tenant_id: str | None = None


_message_context: ContextVar[MessageContext | None] = ContextVar('_message_context', default=None)


def get_message_context() -> MessageContext:
    ctx = _message_context.get()
    if ctx is None:
        msg = 'No active message context. This function must be called within a MessageBus operation.'
        raise RuntimeError(msg)
    return ctx


def try_get_message_context() -> MessageContext | None:
    return _message_context.get()


def _set_message_context(ctx: MessageContext) -> Token[MessageContext | None]:
    return _message_context.set(ctx)


def _reset_message_context(token: Token[MessageContext | None]) -> None:
    _message_context.reset(token)


@contextlib.contextmanager
def message_context_scope(envelope: MessageEnvelope[Any]) -> Generator[None]:
    ctx = MessageContext(
        correlation_id=envelope.correlation_id,
        causation_id=envelope.causation_id,
        message_id=envelope.message_id,
        headers=envelope.headers,
        group_id=envelope.group_id,
        tenant_id=envelope.tenant_id,
    )
    token = _set_message_context(ctx)
    try:
        yield
    finally:
        _reset_message_context(token)
