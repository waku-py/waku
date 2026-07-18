from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from waku.messaging import MessagingError
from waku.messaging.errors.dead_letter import (
    DeadLetterDestinationKind,
    DeadLetterEntry,
    DeadLetterQuery,
    DeadLetterStatus,
    validate_requested_lease,
)


def _from_failure() -> DeadLetterEntry:
    return DeadLetterEntry.from_failure(
        message_type='test.FailedEvent',
        payload={'key': 'value'},
        destination='test://dead',
        destination_kind=DeadLetterDestinationKind.ENDPOINT,
        correlation_id=str(uuid4()),
        causation_id=str(uuid4()),
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
    def test_metadata_field_is_metadata() -> None:
        entry = dataclasses.replace(_from_failure(), metadata={'trace': 'abc'})
        assert entry.metadata == {'trace': 'abc'}
        assert not hasattr(entry, 'metadata_')

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
    def test_replay_lease_fields_must_be_paired() -> None:
        with pytest.raises(MessagingError, match='must both be set or both be None'):
            dataclasses.replace(_from_failure(), replay_owner_id='worker-1')
        with pytest.raises(MessagingError, match='must both be set or both be None'):
            dataclasses.replace(_from_failure(), replay_lease_expires_at=datetime.now(UTC))

    @staticmethod
    def test_from_failure_requires_destination_kind() -> None:
        with pytest.raises(TypeError, match='destination_kind'):
            DeadLetterEntry.from_failure(  # type: ignore[call-arg]
                message_type='test.FailedEvent',
                payload={'key': 'value'},
                destination='test://dead',
                correlation_id=str(uuid4()),
                causation_id=str(uuid4()),
                exc=RuntimeError('boom'),
                attempt=3,
            )

    @staticmethod
    def test_from_failure_threads_destination_kind() -> None:
        entry = DeadLetterEntry.from_failure(
            message_type='test.FailedEvent',
            payload={'key': 'value'},
            destination='tests.messaging.SomeHandler',
            destination_kind=DeadLetterDestinationKind.HANDLER,
            correlation_id=str(uuid4()),
            causation_id=str(uuid4()),
            exc=RuntimeError('boom'),
            attempt=3,
        )
        assert entry.destination_kind is DeadLetterDestinationKind.HANDLER

    @staticmethod
    def test_from_failure_stores_group_id_and_message_id() -> None:
        original_id = uuid4()
        entry = DeadLetterEntry.from_failure(
            message_type='test.FailedEvent',
            payload={'key': 'value'},
            destination='test://dead',
            destination_kind=DeadLetterDestinationKind.ENDPOINT,
            correlation_id=str(uuid4()),
            causation_id=str(uuid4()),
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
        with pytest.raises(MessagingError, match='limit must be >= 0'):
            DeadLetterQuery(limit=-1)

    @staticmethod
    def test_negative_offset_rejected() -> None:
        with pytest.raises(MessagingError, match='offset must be >= 0'):
            DeadLetterQuery(offset=-1)


@pytest.mark.parametrize(
    'lease_expires_at_delta',
    [timedelta(0), timedelta(microseconds=-1)],
)
def test_requested_lease_must_expire_after_now(lease_expires_at_delta: timedelta) -> None:
    now = datetime.now(UTC)
    with pytest.raises(MessagingError, match='lease_expires_at must be greater than now'):
        validate_requested_lease(now, now + lease_expires_at_delta)
