from __future__ import annotations

import dataclasses
from uuid import uuid4

import pytest

from waku.messaging.errors.dead_letter import DeadLetterEntry, DeadLetterStatus


def _from_failure() -> DeadLetterEntry:
    return DeadLetterEntry.from_failure(
        message_type='test.FailedEvent',
        payload={'key': 'value'},
        destination='test://dead',
        correlation_id=uuid4(),
        causation_id=uuid4(),
        exc=RuntimeError('boom'),
        attempt=3,
    )


class TestDeadLetterStatus:
    @staticmethod
    def test_members_are_name_valued_strings() -> None:
        assert DeadLetterStatus.PENDING == 'PENDING'
        assert DeadLetterStatus.REPLAYED == 'REPLAYED'
        assert DeadLetterStatus.REPLAY_FAILED == 'REPLAY_FAILED'


class TestDeadLetterEntry:
    @staticmethod
    def test_from_failure_enters_as_pending_with_zero_replays() -> None:
        entry = _from_failure()
        assert entry.status is DeadLetterStatus.PENDING
        assert entry.replay_count == 0
        assert entry.retry_count == 3
        assert entry.error_message == 'boom'

    @staticmethod
    def test_entry_is_frozen() -> None:
        entry = _from_failure()
        with pytest.raises(dataclasses.FrozenInstanceError):
            entry.status = DeadLetterStatus.REPLAYED  # type: ignore[misc]
