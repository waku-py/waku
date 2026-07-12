from __future__ import annotations

import pytest

from waku.messages import IMessage
from waku.messaging.context import (
    get_message_context,
    message_context_scope,
    try_get_message_context,
)

from tests.messaging.helpers import make_envelope


class _SampleMessage(IMessage):
    pass


def test_get_raises_when_no_context_active() -> None:
    with pytest.raises(RuntimeError, match='No active message context'):
        get_message_context()


def test_try_get_returns_none_when_no_context_active() -> None:
    assert try_get_message_context() is None


def test_scope_activates_context_matching_envelope() -> None:
    envelope = make_envelope(_SampleMessage())
    with message_context_scope(envelope):
        ctx = get_message_context()
        assert ctx.correlation_id == envelope.correlation_id
        assert ctx.causation_id == envelope.causation_id
        assert ctx.message_id == envelope.message_id


def test_try_get_returns_active_context_within_scope() -> None:
    envelope = make_envelope(_SampleMessage())
    with message_context_scope(envelope):
        active = try_get_message_context()
        assert active is not None
        assert active.message_id == envelope.message_id


def test_nested_scope_restores_outer_context_on_exit() -> None:
    outer = make_envelope(_SampleMessage())
    inner = make_envelope(_SampleMessage())
    with message_context_scope(outer):
        assert get_message_context().message_id == outer.message_id
        with message_context_scope(inner):
            assert get_message_context().message_id == inner.message_id
        assert get_message_context().message_id == outer.message_id


def test_scope_copies_group_id_from_envelope() -> None:
    envelope = make_envelope(_SampleMessage(), group_id='order-7')
    with message_context_scope(envelope):
        assert get_message_context().group_id == 'order-7'


def test_scope_defaults_group_id_to_none_when_envelope_has_none() -> None:
    envelope = make_envelope(_SampleMessage())
    with message_context_scope(envelope):
        assert get_message_context().group_id is None
