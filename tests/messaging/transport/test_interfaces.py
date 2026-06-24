from __future__ import annotations

from waku.messaging.transport.interfaces import WireMetadata


class TestWireMetadataHeaders:
    @staticmethod
    def test_as_headers_contains_only_correlation_fields_and_group_id_defaults_none() -> None:
        md = WireMetadata(message_id='m', correlation_id='c', causation_id='x', message_type='T')
        assert md.group_id is None
        assert md.as_headers() == {
            'message_id': 'm',
            'correlation_id': 'c',
            'causation_id': 'x',
            'message_type': 'T',
        }

    @staticmethod
    def test_group_id_is_never_serialized_as_a_header() -> None:
        # group_id is the partition-routing key (Kafka message key), not a broker header.
        md = WireMetadata(message_id='m', correlation_id='c', causation_id='x', message_type='T', group_id='g1')
        assert 'group_id' not in md.as_headers()
        assert md.as_headers() == {
            'message_id': 'm',
            'correlation_id': 'c',
            'causation_id': 'x',
            'message_type': 'T',
        }
