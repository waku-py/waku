from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from uuid import uuid4

import pytest

from waku._internal.retort import default_retort
from waku.messages import IEvent
from waku.messaging._internal.identity import MessageTypeRegistry
from waku.messaging.contracts.envelope import MessageEnvelope
from waku.messaging.errors.dead_letter import DeadLetterEntry
from waku.messaging.inbox.models import InboxEntry
from waku.messaging.outbox.models import OutboxMessage
from waku.messaging.transport._internal.wire import (
    encode_payload,
    envelope_metadata_of,
    rebuild_envelope,
    wire_metadata_from_entry,
)
from waku.messaging.transport.interfaces import EnvelopeMetadata
from waku.serialization import UpcasterChain, upcast
from waku.serialization.codec import PayloadCodec


@dataclass(frozen=True, slots=True)
class OrderPlaced(IEvent):
    order_id: str
    total: float


def _make_registry(*types: type) -> MessageTypeRegistry:
    return MessageTypeRegistry(identities={OrderPlaced: 'order-placed'}, known_types=list(types))


def _make_codec(chain: UpcasterChain | None = None) -> PayloadCodec:
    return PayloadCodec(default_retort, chain or UpcasterChain({}))


def _make_envelope(
    *,
    message_version: int = 1,
    headers: dict[str, str] | None = None,
    group_id: str | None = None,
    scheduled_time: datetime | None = None,
    expires_at: datetime | None = None,
) -> MessageEnvelope[OrderPlaced]:
    return MessageEnvelope(
        message_id=uuid4(),
        correlation_id=str(uuid4()),
        causation_id=str(uuid4()),
        message_type='order-placed',
        message_version=message_version,
        timestamp=datetime.now(tz=UTC),
        payload=OrderPlaced(order_id='o-1', total=42.0),
        headers=headers or {},
        group_id=group_id,
        scheduled_time=scheduled_time,
        expires_at=expires_at,
    )


class TestEncodePayload:
    @staticmethod
    def test_returns_dict_with_payload_fields() -> None:
        codec = _make_codec()
        env = _make_envelope()

        result = encode_payload(env, codec)

        assert result == {'order_id': 'o-1', 'total': 42.0}


class TestEnvelopeMetadataOf:
    @staticmethod
    def test_uuids_become_strings() -> None:
        env = _make_envelope()

        meta = envelope_metadata_of(env)

        assert meta.message_id == str(env.message_id)
        assert meta.correlation_id == str(env.correlation_id)
        assert meta.causation_id == str(env.causation_id)

    @staticmethod
    def test_scalar_fields_pass_through() -> None:
        env = _make_envelope(message_version=2, group_id='grp-1')

        meta = envelope_metadata_of(env)

        assert meta.message_type == 'order-placed'
        assert meta.message_version == 2
        assert meta.group_id == 'grp-1'

    @staticmethod
    def test_headers_copied_to_dict() -> None:
        env = _make_envelope(headers={'tenant': 'acme', 'trace': 'abc'})

        meta = envelope_metadata_of(env)

        assert meta.headers == {'tenant': 'acme', 'trace': 'abc'}

    @staticmethod
    def test_timestamp_stays_as_datetime() -> None:
        env = _make_envelope()

        meta = envelope_metadata_of(env)

        assert isinstance(meta.timestamp, datetime)
        assert meta.timestamp == env.timestamp

    @staticmethod
    def test_scheduled_time_and_expires_at_when_set() -> None:
        scheduled = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)
        expires = datetime(2026, 7, 1, 11, 0, tzinfo=UTC)
        env = _make_envelope(scheduled_time=scheduled, expires_at=expires)

        meta = envelope_metadata_of(env)

        assert meta.scheduled_time == scheduled
        assert meta.expires_at == expires

    @staticmethod
    def test_scheduled_time_and_expires_at_none_when_absent() -> None:
        env = _make_envelope()

        meta = envelope_metadata_of(env)

        assert meta.scheduled_time is None
        assert meta.expires_at is None

    @staticmethod
    def test_group_id_none_when_absent() -> None:
        env = _make_envelope()

        meta = envelope_metadata_of(env)

        assert meta.group_id is None


class TestRebuildEnvelope:
    @staticmethod
    def test_round_trip_identity() -> None:
        registry = _make_registry(OrderPlaced)
        codec = _make_codec()
        env = _make_envelope(headers={'x': 'y'}, group_id='g-1')

        payload_dict = encode_payload(env, codec)
        meta = envelope_metadata_of(env)
        restored = rebuild_envelope(payload_dict, meta, codec, registry)

        assert restored.message_id == env.message_id
        assert restored.correlation_id == env.correlation_id
        assert restored.causation_id == env.causation_id
        assert restored.message_type == env.message_type
        assert restored.message_version == env.message_version
        assert restored.timestamp == env.timestamp
        assert restored.headers == dict(env.headers)
        assert restored.group_id == env.group_id
        assert isinstance(restored.payload, OrderPlaced)
        assert restored.payload == env.payload
        assert restored.scheduled_time == env.scheduled_time
        assert restored.expires_at == env.expires_at

    @staticmethod
    def test_round_trip_with_scheduled_and_expires() -> None:
        registry = _make_registry(OrderPlaced)
        codec = _make_codec()
        tz_plus5 = timezone(timedelta(hours=5))
        scheduled = datetime(2026, 7, 1, 10, 0, tzinfo=tz_plus5)
        expires = datetime(2026, 7, 1, 11, 0, tzinfo=tz_plus5)
        env = _make_envelope(scheduled_time=scheduled, expires_at=expires)

        restored = rebuild_envelope(encode_payload(env, codec), envelope_metadata_of(env), codec, registry)

        assert restored.scheduled_time == scheduled.astimezone(UTC)
        assert restored.expires_at == expires.astimezone(UTC)

    @staticmethod
    def test_envelope_metadata_round_trips_all_fields() -> None:
        registry = _make_registry(OrderPlaced)
        codec = _make_codec()
        scheduled = datetime(2026, 8, 1, 9, 30, tzinfo=UTC)
        expires = datetime(2026, 8, 1, 18, 45, tzinfo=UTC)
        env = _make_envelope(
            message_version=7,
            headers={'trace': 'abc'},
            group_id='g-1',
            scheduled_time=scheduled,
            expires_at=expires,
        )

        restored = rebuild_envelope(encode_payload(env, codec), envelope_metadata_of(env), codec, registry)

        assert restored.message_id == env.message_id
        assert restored.correlation_id == env.correlation_id
        assert restored.causation_id == env.causation_id
        assert restored.message_type == env.message_type
        assert restored.message_version == 7
        assert restored.timestamp == env.timestamp.astimezone(UTC)
        assert restored.headers == {'trace': 'abc'}
        assert restored.group_id == 'g-1'
        assert restored.payload == env.payload
        assert restored.scheduled_time == scheduled
        assert restored.expires_at == expires

    @staticmethod
    def test_preserves_non_uuid_correlation_and_causation() -> None:
        registry = _make_registry(OrderPlaced)
        codec = _make_codec()
        env = _make_envelope()
        meta = dataclasses.replace(
            envelope_metadata_of(env),
            correlation_id='trace-abc-123',
            causation_id='req-xyz-789',
        )

        restored = rebuild_envelope(encode_payload(env, codec), meta, codec, registry)

        assert restored.correlation_id == 'trace-abc-123'
        assert restored.causation_id == 'req-xyz-789'

    @staticmethod
    def test_round_trip_preserves_non_uuid_ids() -> None:
        registry = _make_registry(OrderPlaced)
        codec = _make_codec()
        env = dataclasses.replace(
            _make_envelope(),
            correlation_id='trace-abc-123',
            causation_id='req-xyz-789',
        )

        restored = rebuild_envelope(encode_payload(env, codec), envelope_metadata_of(env), codec, registry)

        assert restored.correlation_id == env.correlation_id
        assert restored.causation_id == env.causation_id

    @staticmethod
    def test_unknown_message_type_raises_value_error() -> None:
        registry = _make_registry(OrderPlaced)
        codec = _make_codec()
        env = _make_envelope()

        payload_dict = encode_payload(env, codec)
        meta = EnvelopeMetadata(
            message_id=str(uuid4()),
            correlation_id=str(uuid4()),
            causation_id=str(uuid4()),
            message_type='unknown.Type',
            message_version=1,
            timestamp=datetime.now(tz=UTC),
        )

        with pytest.raises(ValueError, match='Unknown message type'):
            rebuild_envelope(payload_dict, meta, codec, registry)

    @staticmethod
    def test_null_timestamp_raises_value_error() -> None:
        registry = _make_registry(OrderPlaced)
        codec = _make_codec()
        env = _make_envelope()
        payload_dict = encode_payload(env, codec)
        meta = EnvelopeMetadata(
            message_id=str(uuid4()),
            correlation_id=str(uuid4()),
            causation_id=str(uuid4()),
            message_type='order-placed',
            message_version=1,
            timestamp=None,
        )

        with pytest.raises(ValueError, match='non-None timestamp'):
            rebuild_envelope(payload_dict, meta, codec, registry)

    @staticmethod
    def test_timestamp_normalised_to_utc() -> None:
        registry = _make_registry(OrderPlaced)
        codec = _make_codec()
        env = _make_envelope()
        tz_plus5 = timezone(timedelta(hours=5))
        non_utc_ts = datetime(2026, 1, 1, 12, 0, tzinfo=tz_plus5)

        meta = dataclasses.replace(envelope_metadata_of(env), timestamp=non_utc_ts)
        payload_dict = encode_payload(env, codec)
        restored = rebuild_envelope(payload_dict, meta, codec, registry)

        assert restored.timestamp.tzinfo is UTC
        assert restored.timestamp == non_utc_ts.astimezone(UTC)

    @staticmethod
    def test_versioned_payload_with_upcaster_genuinely_exercises_chain() -> None:
        # NON-VACUOUS version=2 test. The upcaster UNCONDITIONALLY overwrites total -> 0.0 whenever it runs.
        # The envelope is already at version 2, so the chain must SKIP the from_version=1 upcaster
        # (schema_version 2 > 1) and total stays 99.5. If the version were not threaded through codec.decode
        # (e.g. treated as v1), the upcaster WOULD run and overwrite total to 0.0 -> the 99.5 assertion below
        # would fail. That sensitivity is what makes this test non-vacuous.
        chain = UpcasterChain({'order-placed': [upcast(from_version=1, fn=lambda d: {**d, 'total': 0.0})]})
        registry = _make_registry(OrderPlaced)
        codec = _make_codec(chain)

        # Build an envelope that is already at version 2 with the real total value.
        env = MessageEnvelope(
            message_id=uuid4(),
            correlation_id=str(uuid4()),
            causation_id=str(uuid4()),
            message_type='order-placed',
            message_version=2,
            timestamp=datetime.now(tz=UTC),
            payload=OrderPlaced(order_id='v2-order', total=99.5),
            headers={},
        )

        payload_dict = encode_payload(env, codec)
        meta = envelope_metadata_of(env)
        restored = rebuild_envelope(payload_dict, meta, codec, registry)

        # If upcaster had run on the v2 data, total would have been overwritten with 0.0.
        # The fact that total == 99.5 proves the upcaster was skipped (version already current).
        assert restored.message_version == 2
        assert restored.payload.total == 99.5
        assert restored.payload.order_id == 'v2-order'


def _make_meta_json(
    *,
    message_version: int = 1,
    timestamp: str = '2026-06-29T10:00:00+00:00',
    headers: dict[str, str] | None = None,
    scheduled_time: str | None = None,
    expires_at: str | None = None,
) -> dict[str, object]:
    return {
        'message_version': message_version,
        'timestamp': timestamp,
        'headers': headers or {},
        'scheduled_time': scheduled_time,
        'expires_at': expires_at,
    }


def _make_outbox_message(**overrides: object) -> OutboxMessage:
    corr = str(uuid4())
    caus = str(uuid4())
    defaults: dict[str, object] = {
        'id': uuid4(),
        'idempotency_key': str(uuid4()),
        'message_type': 'test.Event',
        'payload': {'test': True},
        'destination': 'test://dest',
        'correlation_id': corr,
        'causation_id': caus,
    }
    return OutboxMessage(**(defaults | overrides))  # type: ignore[arg-type]


def _make_inbox_entry(**overrides: object) -> InboxEntry:
    corr = str(uuid4())
    caus = str(uuid4())
    defaults: dict[str, object] = {
        'id': uuid4(),
        'payload': {'test': True},
        'message_type': 'test.Event',
        'source_uri': 'local://orders',
        'destination': 'tests.messaging.HandlerA',
        'correlation_id': corr,
        'causation_id': caus,
    }
    return InboxEntry(**(defaults | overrides))  # type: ignore[arg-type]


def _make_dlq_entry(**overrides: object) -> DeadLetterEntry:
    corr = str(uuid4())
    caus = str(uuid4())
    defaults: dict[str, object] = {
        'id': uuid4(),
        'message_type': 'test.FailedEvent',
        'payload': {'key': 'value'},
        'destination': 'test://dead',
        'correlation_id': corr,
        'causation_id': caus,
        'error_type': 'RuntimeError',
        'error_message': 'boom',
        'retry_count': 1,
    }
    return DeadLetterEntry(**(defaults | overrides))  # type: ignore[arg-type]


class TestWireMetadataFromEntry:
    @staticmethod
    def test_outbox_uses_idempotency_key_as_message_id() -> None:
        idem = str(uuid4())
        entry = _make_outbox_message(idempotency_key=idem, metadata_=_make_meta_json())

        result = wire_metadata_from_entry(entry)

        assert result.message_id == idem

    @staticmethod
    def test_inbox_uses_str_id_as_message_id() -> None:
        entry_id = uuid4()
        entry = _make_inbox_entry(id=entry_id, metadata_=_make_meta_json())

        result = wire_metadata_from_entry(entry)

        assert result.message_id == str(entry_id)

    @staticmethod
    def test_dlq_uses_message_id_column_when_set() -> None:
        original_message_id = uuid4()
        entry = _make_dlq_entry(message_id=original_message_id, metadata_=_make_meta_json())

        result = wire_metadata_from_entry(entry)

        assert result.message_id == str(original_message_id)

    @staticmethod
    def test_dlq_falls_back_to_entry_id_when_message_id_column_is_null() -> None:
        # Legacy DLQ rows written before the message_id column existed have message_id=None.
        entry_id = uuid4()
        entry = _make_dlq_entry(id=entry_id, message_id=None, metadata_=_make_meta_json())

        result = wire_metadata_from_entry(entry)

        assert result.message_id == str(entry_id)

    @staticmethod
    def test_typed_columns_mapped_for_outbox() -> None:
        corr = str(uuid4())
        caus = str(uuid4())
        entry = _make_outbox_message(
            correlation_id=corr,
            causation_id=caus,
            group_id='grp-1',
            message_type='orders.OrderPlaced',
            metadata_=_make_meta_json(),
        )

        result = wire_metadata_from_entry(entry)

        assert result.correlation_id == str(corr)
        assert result.causation_id == str(caus)
        assert result.group_id == 'grp-1'
        assert result.message_type == 'orders.OrderPlaced'

    @staticmethod
    def test_typed_columns_mapped_for_inbox() -> None:
        corr = str(uuid4())
        caus = str(uuid4())
        entry = _make_inbox_entry(
            correlation_id=corr,
            causation_id=caus,
            group_id='grp-2',
            message_type='orders.OrderShipped',
            metadata_=_make_meta_json(),
        )

        result = wire_metadata_from_entry(entry)

        assert result.correlation_id == str(corr)
        assert result.causation_id == str(caus)
        assert result.group_id == 'grp-2'
        assert result.message_type == 'orders.OrderShipped'

    @staticmethod
    def test_metadata_json_parsed_for_version_timestamp_headers() -> None:
        ts_str = '2026-06-29T10:00:00+00:00'
        meta = _make_meta_json(message_version=3, timestamp=ts_str, headers={'x-tenant': 'acme'})
        entry = _make_outbox_message(metadata_=meta)

        result = wire_metadata_from_entry(entry)

        assert result.message_version == 3
        assert result.timestamp == datetime.fromisoformat(ts_str)
        assert result.headers == {'x-tenant': 'acme'}

    @staticmethod
    def test_scheduled_time_and_expires_at_parsed_from_isoformat() -> None:
        sched_str = '2026-07-01T10:00:00+00:00'
        exp_str = '2026-07-01T11:00:00+00:00'
        meta = _make_meta_json(scheduled_time=sched_str, expires_at=exp_str)
        entry = _make_outbox_message(metadata_=meta)

        result = wire_metadata_from_entry(entry)

        assert result.scheduled_time == datetime.fromisoformat(sched_str)
        assert result.expires_at == datetime.fromisoformat(exp_str)

    @staticmethod
    def test_none_metadata_returns_minimal_with_typed_columns() -> None:
        corr = str(uuid4())
        caus = str(uuid4())
        entry = _make_outbox_message(correlation_id=corr, causation_id=caus, metadata_=None)

        result = wire_metadata_from_entry(entry)

        # Must not raise; typed columns still present.
        assert result.correlation_id == str(corr)
        assert result.causation_id == str(caus)
        assert result.message_version == 1
        assert result.timestamp is None
        assert result.headers == {}
        assert result.scheduled_time is None
        assert result.expires_at is None

    @staticmethod
    def test_corrupt_metadata_returns_minimal_with_typed_columns() -> None:
        corr = str(uuid4())
        caus = str(uuid4())
        # Corrupt: timestamp value is not a valid isoformat string.
        corrupt_meta = {'message_version': 1, 'timestamp': 'NOT-A-DATE', 'headers': {}}
        entry = _make_inbox_entry(correlation_id=corr, causation_id=caus, metadata_=corrupt_meta)

        result = wire_metadata_from_entry(entry)

        # Must not raise; falls back to minimal — correlation/causation still readable.
        assert result.correlation_id == str(corr)
        assert result.causation_id == str(caus)
        assert result.message_version == 1
        assert result.timestamp is None

    @staticmethod
    def test_corrupt_timestamp_preserves_message_version_and_headers() -> None:
        # Per-field fault tolerance: a corrupt timestamp must NOT revert message_version to 1
        # (wrong version → wrong upcasting → data corruption). Each field parses independently.
        corrupt_meta: dict[str, object] = {
            'message_version': 2,
            'timestamp': 'NOT-A-DATE',
            'headers': {'x-tenant': 'acme'},
        }
        entry = _make_outbox_message(metadata_=corrupt_meta)

        result = wire_metadata_from_entry(entry)

        assert result.message_version == 2
        assert result.headers == {'x-tenant': 'acme'}
        assert result.timestamp is None

    @staticmethod
    def test_null_correlation_and_causation_fall_back_to_entry_id() -> None:
        # Legacy rows with NULL correlation_id/causation_id must not produce '' — UUID('') crashes
        # rebuild_envelope. The fallback to str(entry.id) yields a valid UUID string instead.
        entry = _make_inbox_entry(correlation_id=None, causation_id=None, metadata_=_make_meta_json())

        result = wire_metadata_from_entry(entry)

        assert result.correlation_id == str(entry.id)
        assert result.causation_id == str(entry.id)

    @staticmethod
    def test_dlq_group_id_from_typed_column() -> None:
        entry = _make_dlq_entry(group_id='partition-99', metadata_=_make_meta_json())

        result = wire_metadata_from_entry(entry)

        assert result.group_id == 'partition-99'
