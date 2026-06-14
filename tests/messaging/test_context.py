from __future__ import annotations

from uuid import uuid4

import pytest

from waku.messaging.context import (
    MessageContext,
    get_message_context,
    message_context_scope,
    reset_message_context,
    set_message_context,
    try_get_message_context,
)
from waku.messaging.contracts.message import IMessage

from tests.messaging.helpers import make_envelope


class _SampleMessage(IMessage):
    pass


def _make_context() -> MessageContext:
    return MessageContext(
        correlation_id=uuid4(),
        causation_id=uuid4(),
        message_id=uuid4(),
        headers={},
    )


def test_get_raises_when_no_context_active() -> None:
    with pytest.raises(RuntimeError, match='No active message context'):
        get_message_context()


def test_set_then_get_returns_same_context() -> None:
    ctx = _make_context()
    token = set_message_context(ctx)
    try:
        assert get_message_context() is ctx
    finally:
        reset_message_context(token)


def test_try_get_returns_none_when_no_context_active() -> None:
    assert try_get_message_context() is None


def test_try_get_returns_context_when_active() -> None:
    ctx = _make_context()
    token = set_message_context(ctx)
    try:
        assert try_get_message_context() is ctx
    finally:
        reset_message_context(token)


def test_token_reset_restores_previous_state() -> None:
    first = _make_context()
    first_token = set_message_context(first)
    try:
        second = _make_context()
        second_token = set_message_context(second)
        assert get_message_context() is second

        reset_message_context(second_token)
        assert get_message_context() is first
    finally:
        reset_message_context(first_token)


def test_scope_copies_group_id_from_envelope() -> None:
    envelope = make_envelope(_SampleMessage(), group_id='order-7')
    with message_context_scope(envelope):
        assert get_message_context().group_id == 'order-7'


def test_scope_defaults_group_id_to_none_when_envelope_has_none() -> None:
    envelope = make_envelope(_SampleMessage())
    with message_context_scope(envelope):
        assert get_message_context().group_id is None
