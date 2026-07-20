from __future__ import annotations

from dataclasses import dataclass

import pytest

from waku.messages import IEvent
from waku.messaging.contracts.request import IRequest
from waku.messaging.outgoing import Action, OutgoingMessages, PendingMessage


@dataclass(frozen=True)
class _SampleEvent(IEvent):
    pass


@dataclass(frozen=True)
class _SampleRequest(IRequest[None]):
    pass


class TestOutgoingMessagesFrameStack:
    @staticmethod
    def test_publish_appends_pending_with_publish_action() -> None:
        outgoing = OutgoingMessages()
        outgoing.push_frame()

        event = _SampleEvent()
        outgoing.publish(event)

        pending = outgoing.pop_frame()
        assert len(pending) == 1
        assert pending[0].message is event
        assert pending[0].action is Action.PUBLISH

    @staticmethod
    def test_send_appends_pending_with_send_action() -> None:
        outgoing = OutgoingMessages()
        outgoing.push_frame()

        request = _SampleRequest()
        outgoing.send(request)

        pending = outgoing.pop_frame()
        assert len(pending) == 1
        assert pending[0].message is request
        assert pending[0].action is Action.SEND

    @staticmethod
    def test_pop_frame_returns_fifo_order() -> None:
        outgoing = OutgoingMessages()
        outgoing.push_frame()

        first = _SampleEvent()
        second = _SampleEvent()
        outgoing.publish(first)
        outgoing.publish(second)

        pending = outgoing.pop_frame()
        assert [p.message for p in pending] == [first, second]

    @staticmethod
    def test_nested_frame_isolates_inner_messages() -> None:
        outgoing = OutgoingMessages()
        outgoing.push_frame()
        outer = _SampleEvent()
        outgoing.publish(outer)

        outgoing.push_frame()
        inner = _SampleEvent()
        outgoing.publish(inner)
        inner_frame = outgoing.pop_frame()

        outer_frame = outgoing.pop_frame()
        assert [p.message for p in inner_frame] == [inner]
        assert [p.message for p in outer_frame] == [outer]

    @staticmethod
    def test_discard_frame_drops_only_current_level() -> None:
        outgoing = OutgoingMessages()
        outgoing.push_frame()
        outer = _SampleEvent()
        outgoing.publish(outer)

        outgoing.push_frame()
        outgoing.publish(_SampleEvent())
        outgoing.discard_frame()

        remaining = outgoing.pop_frame()
        assert [p.message for p in remaining] == [outer]

    @staticmethod
    def test_pending_returns_current_frame_snapshot_before_pop() -> None:
        outgoing = OutgoingMessages()
        outgoing.push_frame()
        outgoing.publish(_SampleEvent())
        outgoing.push_frame()
        inner_event = _SampleEvent()
        outgoing.publish(inner_event)
        # pending reflects only the innermost frame
        assert [p.message for p in outgoing.pending] == [inner_event]

    @staticmethod
    def test_pending_is_empty_when_no_frame_active() -> None:
        outgoing = OutgoingMessages()
        assert outgoing.pending == ()

    @staticmethod
    def test_publish_without_active_frame_raises_clear_error() -> None:
        outgoing = OutgoingMessages()
        with pytest.raises(RuntimeError, match='no active cascade frame'):
            outgoing.publish(_SampleEvent())


class TestOutgoingMessagesDeferredBucket:
    @staticmethod
    def test_drain_current_frame_returns_and_clears_without_popping() -> None:
        outgoing = OutgoingMessages()
        outgoing.push_frame()
        first = _SampleEvent()
        second = _SampleEvent()
        outgoing.publish(first)
        outgoing.publish(second)

        drained = outgoing.drain_current_frame()
        assert [p.message for p in drained] == [first, second]
        # Frame still exists (depth preserved) but is now empty.
        assert outgoing.pop_frame() == []

    @staticmethod
    def test_drain_current_frame_returns_empty_when_no_frame() -> None:
        outgoing = OutgoingMessages()
        assert outgoing.drain_current_frame() == []

    @staticmethod
    def test_detach_deferred_returns_immutable_fifo_effects_and_clears() -> None:
        outgoing = OutgoingMessages()
        first = PendingMessage(message=_SampleEvent(), action=Action.PUBLISH)
        second = PendingMessage(message=_SampleRequest(), action=Action.SEND)
        outgoing.defer([first])
        outgoing.defer([second])

        effects = outgoing.detach_deferred()

        assert effects.messages == (first, second)
        assert effects.sent_evidence == ()
        assert isinstance(effects.messages, tuple)
        assert all(isinstance(pending, PendingMessage) for pending in effects.messages)
        # A second detach yields empty, falsy effects (both buckets cleared).
        drained = outgoing.detach_deferred()
        assert not drained
        assert drained.messages == ()
