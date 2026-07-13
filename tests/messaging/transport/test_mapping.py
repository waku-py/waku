from __future__ import annotations

from datetime import UTC, datetime

import pytest

from waku.messaging.transport.interfaces import EnvelopeMetadata, MalformedMetadataError
from waku.messaging.transport.mapping import (
    WIRE_CONTENT_TYPE,
    UnsupportedContentTypeError,
    metadata_from_headers,
    wire_headers_of,
)

_BASE = EnvelopeMetadata(
    message_id='mid-1',
    correlation_id='corr-1',
    causation_id='cause-1',
    message_type='orders.OrderPlaced',
)


class TestWireHeadersOf:
    @staticmethod
    def test_always_emits_required_fields() -> None:
        headers = wire_headers_of(_BASE)

        assert headers['message_id'] == 'mid-1'
        assert headers['correlation_id'] == 'corr-1'
        assert headers['causation_id'] == 'cause-1'
        assert headers['message_type'] == 'orders.OrderPlaced'
        assert headers['message_version'] == '1'
        assert headers['content-type'] == WIRE_CONTENT_TYPE

    @staticmethod
    def test_optional_datetime_fields_omitted_when_none() -> None:
        headers = wire_headers_of(_BASE)

        assert 'timestamp' not in headers
        assert 'scheduled_time' not in headers
        assert 'expires_at' not in headers
        assert 'group_id' not in headers

    @staticmethod
    def test_optional_datetime_fields_emitted_as_isoformat_when_set() -> None:
        ts = datetime(2025, 1, 15, 12, 0, 0, tzinfo=UTC)
        scheduled = datetime(2025, 1, 16, 8, 0, 0, tzinfo=UTC)
        expires = datetime(2025, 1, 16, 9, 0, 0, tzinfo=UTC)
        meta = EnvelopeMetadata(
            message_id='m',
            correlation_id='c',
            causation_id='x',
            message_type='t',
            timestamp=ts,
            scheduled_time=scheduled,
            expires_at=expires,
        )

        headers = wire_headers_of(meta)

        assert headers['timestamp'] == ts.isoformat()
        assert headers['scheduled_time'] == scheduled.isoformat()
        assert headers['expires_at'] == expires.isoformat()

    @staticmethod
    def test_group_id_emitted_when_set() -> None:
        meta = EnvelopeMetadata(
            message_id='m',
            correlation_id='c',
            causation_id='x',
            message_type='t',
            group_id='order-42',
        )

        headers = wire_headers_of(meta)

        assert headers['group_id'] == 'order-42'

    @staticmethod
    def test_group_id_omitted_when_none() -> None:
        headers = wire_headers_of(_BASE)

        assert 'group_id' not in headers

    @staticmethod
    def test_tenant_id_emitted_when_set() -> None:
        meta = EnvelopeMetadata(
            message_id='m',
            correlation_id='c',
            causation_id='x',
            message_type='t',
            tenant_id='t-acme',
        )

        headers = wire_headers_of(meta)

        assert headers['tenant_id'] == 't-acme'

    @staticmethod
    def test_none_tenant_emits_no_header() -> None:
        headers = wire_headers_of(_BASE)

        assert 'tenant_id' not in headers

    @staticmethod
    def test_user_header_named_tenant_id_is_dropped() -> None:
        meta = EnvelopeMetadata(
            message_id='m',
            correlation_id='c',
            causation_id='x',
            message_type='t',
            tenant_id='real',
            headers={'tenant_id': 'spoof'},
        )

        headers = wire_headers_of(meta)

        assert headers['tenant_id'] == 'real'  # reserved wins; the user value is dropped

    @staticmethod
    def test_user_headers_emitted_bare_no_prefix() -> None:
        meta = EnvelopeMetadata(
            message_id='m',
            correlation_id='c',
            causation_id='x',
            message_type='t',
            headers={'foo': 'bar', 'tenant': 'acme'},
        )

        headers = wire_headers_of(meta)

        assert headers['foo'] == 'bar'
        assert headers['tenant'] == 'acme'
        # No h. prefix anywhere
        assert not any(k.startswith('h.') for k in headers)

    @staticmethod
    def test_user_header_colliding_with_reserved_key_is_skipped() -> None:
        meta = EnvelopeMetadata(
            message_id='m',
            correlation_id='c',
            causation_id='x',
            message_type='real-type',
            headers={'message_type': 'user-override-attempt', 'foo': 'bar'},
        )

        headers = wire_headers_of(meta)

        # Reserved key wins — user value dropped, not overwritten
        assert headers['message_type'] == 'real-type'
        # Non-reserved user header passes through bare
        assert headers['foo'] == 'bar'

    @staticmethod
    def test_all_reserved_keys_skip_when_supplied_as_user_headers() -> None:
        reserved_as_user = {
            'message_id': 'u-mid',
            'correlation_id': 'u-corr',
            'causation_id': 'u-cause',
            'message_type': 'u-type',
            'message_version': '99',
            'timestamp': '2099-01-01T00:00:00',
            'scheduled_time': '2099-01-01T00:00:00',
            'expires_at': '2099-01-01T00:00:00',
            'content-type': 'application/xml',
            'group_id': 'u-group',
        }
        meta = EnvelopeMetadata(
            message_id='real-mid',
            correlation_id='real-corr',
            causation_id='real-cause',
            message_type='real-type',
            headers=reserved_as_user,
        )

        headers = wire_headers_of(meta)

        assert headers['message_id'] == 'real-mid'
        assert headers['message_type'] == 'real-type'
        assert headers['content-type'] == WIRE_CONTENT_TYPE
        # All came from the real fields, not user overrides
        assert headers['message_version'] == '1'


class TestMetadataFromHeaders:
    @staticmethod
    def test_round_trip_reconstructs_required_fields() -> None:
        headers = wire_headers_of(_BASE)
        meta = metadata_from_headers(headers)

        assert meta.message_id == 'mid-1'
        assert meta.correlation_id == 'corr-1'
        assert meta.causation_id == 'cause-1'
        assert meta.message_type == 'orders.OrderPlaced'
        assert meta.message_version == 1

    @staticmethod
    def test_round_trip_with_group_id() -> None:
        orig = EnvelopeMetadata(
            message_id='m',
            correlation_id='c',
            causation_id='x',
            message_type='t',
            group_id='order-99',
        )
        headers = wire_headers_of(orig)
        meta = metadata_from_headers(headers)

        assert meta.group_id == 'order-99'

    @staticmethod
    def test_round_trip_with_group_id_none() -> None:
        headers = wire_headers_of(_BASE)
        meta = metadata_from_headers(headers)

        assert meta.group_id is None

    @staticmethod
    def test_tenant_id_round_trips_through_headers() -> None:
        orig = EnvelopeMetadata(
            message_id='m',
            correlation_id='c',
            causation_id='x',
            message_type='t',
            tenant_id='t-acme',
        )
        headers = wire_headers_of(orig)
        meta = metadata_from_headers(headers)

        assert meta.tenant_id == 't-acme'

    @staticmethod
    def test_round_trip_with_tenant_id_none() -> None:
        headers = wire_headers_of(_BASE)
        meta = metadata_from_headers(headers)

        assert meta.tenant_id is None

    @staticmethod
    def test_round_trip_with_datetime_optionals() -> None:
        ts = datetime(2025, 6, 1, 10, 0, 0, tzinfo=UTC)
        orig = EnvelopeMetadata(
            message_id='m',
            correlation_id='c',
            causation_id='x',
            message_type='t',
            timestamp=ts,
        )
        headers = wire_headers_of(orig)
        meta = metadata_from_headers(headers)

        assert meta.timestamp == ts
        assert meta.scheduled_time is None
        assert meta.expires_at is None

    @staticmethod
    def test_user_headers_routed_back_into_headers_dict() -> None:
        orig = EnvelopeMetadata(
            message_id='m',
            correlation_id='c',
            causation_id='x',
            message_type='t',
            headers={'foo': 'bar'},
        )
        headers = wire_headers_of(orig)
        meta = metadata_from_headers(headers)

        assert meta.headers == {'foo': 'bar'}

    @staticmethod
    def test_content_type_consumed_not_echoed_as_user_header() -> None:
        orig = EnvelopeMetadata(
            message_id='m',
            correlation_id='c',
            causation_id='x',
            message_type='t',
            headers={'foo': 'bar'},
        )
        headers = wire_headers_of(orig)
        meta = metadata_from_headers(headers)

        assert 'content-type' not in meta.headers

    @staticmethod
    def test_message_version_cast_to_int() -> None:
        headers = {
            'message_id': 'm',
            'correlation_id': 'c',
            'causation_id': 'x',
            'message_type': 't',
            'message_version': '3',
            'content-type': WIRE_CONTENT_TYPE,
        }
        meta = metadata_from_headers(headers)

        assert meta.message_version == 3

    @staticmethod
    def test_non_numeric_message_version_raises() -> None:
        # A present-but-non-numeric reserved message_version is poison (would upcast wrong), treated
        # exactly like a foreign content-type — the inbound adapter REJECTs.
        headers = {
            'message_id': 'm',
            'correlation_id': 'c',
            'causation_id': 'x',
            'message_type': 't',
            'message_version': 'bad-version',
            'content-type': WIRE_CONTENT_TYPE,
        }

        with pytest.raises(MalformedMetadataError):
            metadata_from_headers(headers)

    @staticmethod
    def test_absent_required_reserved_header_raises() -> None:
        # message_id/correlation_id/causation_id/message_type are always emitted by wire_headers_of.
        # An absent one on the default mapper is poison (was: '' flowing to UUID('')), naming the key.
        for missing in ('message_id', 'correlation_id', 'causation_id', 'message_type'):
            headers = {
                'message_id': 'm',
                'correlation_id': 'c',
                'causation_id': 'x',
                'message_type': 't',
                'content-type': WIRE_CONTENT_TYPE,
            }
            del headers[missing]

            with pytest.raises(MalformedMetadataError, match=missing):
                metadata_from_headers(headers)

    @staticmethod
    def test_absent_content_type_is_lenient() -> None:
        # Older Kafka brokers may omit the content-type header; fall back to JSON decoding rather than
        # rejecting the message. This matches the Wolverine lenient-inbound behaviour.
        headers = {
            'message_id': 'm',
            'correlation_id': 'c',
            'causation_id': 'x',
            'message_type': 't',
        }
        meta = metadata_from_headers(headers)

        assert meta.message_id == 'm'

    @staticmethod
    def test_foreign_content_type_raises_unsupported_content_type_error() -> None:
        headers = {
            'message_id': 'm',
            'correlation_id': 'c',
            'causation_id': 'x',
            'message_type': 't',
            'content-type': 'application/xml',
        }

        with pytest.raises(UnsupportedContentTypeError):
            metadata_from_headers(headers)

    @staticmethod
    def test_binary_content_type_raises_unsupported_content_type_error() -> None:
        headers = {
            'message_id': 'm',
            'correlation_id': 'c',
            'causation_id': 'x',
            'message_type': 't',
            'content-type': 'application/octet-stream',
        }

        with pytest.raises(UnsupportedContentTypeError):
            metadata_from_headers(headers)


class TestRoundTripIdentity:
    @staticmethod
    def test_full_round_trip_all_fields_set() -> None:
        ts = datetime(2025, 3, 1, 0, 0, 0, tzinfo=UTC)
        orig = EnvelopeMetadata(
            message_id='mid-42',
            correlation_id='corr-42',
            causation_id='cause-42',
            message_type='domain.SomeEvent',
            group_id='tenant-7',
            message_version=5,
            timestamp=ts,
            headers={'x-request-id': 'req-99'},
        )

        headers = wire_headers_of(orig)
        meta = metadata_from_headers(headers)

        assert meta.message_id == orig.message_id
        assert meta.correlation_id == orig.correlation_id
        assert meta.causation_id == orig.causation_id
        assert meta.message_type == orig.message_type
        assert meta.group_id == orig.group_id
        assert meta.message_version == orig.message_version
        assert meta.timestamp == orig.timestamp
        assert meta.headers == dict(orig.headers)
