from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from waku.messaging.transport.interfaces import EnvelopeMetadata


class TestEnvelopeMetadataNewFields:
    @staticmethod
    def test_message_version_defaults_to_1() -> None:
        md = EnvelopeMetadata(message_id='m', correlation_id='c', causation_id='x', message_type='T')
        assert md.message_version == 1

    @staticmethod
    def test_timestamp_defaults_to_none() -> None:
        md = EnvelopeMetadata(message_id='m', correlation_id='c', causation_id='x', message_type='T')
        assert md.timestamp is None

    @staticmethod
    def test_scheduled_time_defaults_to_none() -> None:
        md = EnvelopeMetadata(message_id='m', correlation_id='c', causation_id='x', message_type='T')
        assert md.scheduled_time is None

    @staticmethod
    def test_expires_at_defaults_to_none() -> None:
        md = EnvelopeMetadata(message_id='m', correlation_id='c', causation_id='x', message_type='T')
        assert md.expires_at is None

    @staticmethod
    def test_headers_defaults_to_empty_dict() -> None:
        md = EnvelopeMetadata(message_id='m', correlation_id='c', causation_id='x', message_type='T')
        assert md.headers == {}

    @staticmethod
    def test_headers_default_is_not_shared_between_instances() -> None:
        md1 = EnvelopeMetadata(message_id='m', correlation_id='c', causation_id='x', message_type='T')
        md2 = EnvelopeMetadata(message_id='m', correlation_id='c', causation_id='x', message_type='T')
        assert md1.headers is not md2.headers

    @staticmethod
    def test_frozen_raises_on_attribute_set() -> None:
        md = EnvelopeMetadata(message_id='m', correlation_id='c', causation_id='x', message_type='T')
        with pytest.raises(FrozenInstanceError):
            md.message_version = 99  # type: ignore[misc]
