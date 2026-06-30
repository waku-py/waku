from __future__ import annotations

import dataclasses
from uuid import uuid4

import pytest

from waku.messaging.errors.dead_letter import DeadLetterEntry, DeadLetterQuery, DeadLetterStatus


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

    @staticmethod
    def test_from_failure_stores_group_id_and_message_id() -> None:
        original_id = uuid4()
        entry = DeadLetterEntry.from_failure(
            message_type='test.FailedEvent',
            payload={'key': 'value'},
            destination='test://dead',
            correlation_id=uuid4(),
            causation_id=uuid4(),
            exc=RuntimeError('boom'),
            attempt=3,
            group_id='partition-42',
            message_id=original_id,
        )
        assert entry.group_id == 'partition-42'
        assert entry.message_id == original_id


class TestDeadLetterQuery:
    @staticmethod
    def test_defaults_and_zero_limit_are_valid() -> None:
        assert DeadLetterQuery().limit == 100
        assert DeadLetterQuery(limit=0).limit == 0

    @staticmethod
    def test_negative_limit_rejected() -> None:
        with pytest.raises(ValueError, match='limit must be >= 0'):
            DeadLetterQuery(limit=-1)

    @staticmethod
    def test_negative_offset_rejected() -> None:
        with pytest.raises(ValueError, match='offset must be >= 0'):
            DeadLetterQuery(offset=-1)
