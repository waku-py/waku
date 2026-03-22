from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from waku.messaging.contracts.factory import EnvelopeFactory


class SampleMessage:
    pass


class TestEnvelopeFactory:
    @staticmethod
    def test_create_generates_uuid_message_id() -> None:
        envelope = EnvelopeFactory.create(SampleMessage())

        assert isinstance(envelope.message_id, UUID)

    @staticmethod
    def test_create_generates_uuid_correlation_id_when_not_provided() -> None:
        envelope = EnvelopeFactory.create(SampleMessage())

        assert isinstance(envelope.correlation_id, UUID)
        assert envelope.correlation_id != envelope.message_id

    @staticmethod
    def test_create_sets_causation_id_to_message_id_when_not_provided() -> None:
        envelope = EnvelopeFactory.create(SampleMessage())

        assert envelope.causation_id == envelope.message_id

    @staticmethod
    def test_create_sets_message_type_to_fully_qualified_name() -> None:
        envelope = EnvelopeFactory.create(SampleMessage())

        expected = f'{SampleMessage.__module__}.{SampleMessage.__qualname__}'
        assert envelope.message_type == expected

    @staticmethod
    def test_create_sets_utc_timestamp() -> None:
        before = datetime.now(tz=UTC)
        envelope = EnvelopeFactory.create(SampleMessage())
        after = datetime.now(tz=UTC)

        assert before <= envelope.timestamp <= after

    @staticmethod
    def test_create_stores_message_as_payload() -> None:
        message = SampleMessage()

        envelope = EnvelopeFactory.create(message)

        assert envelope.payload is message

    @staticmethod
    def test_create_uses_empty_dict_for_headers_when_not_provided() -> None:
        envelope = EnvelopeFactory.create(SampleMessage())

        assert envelope.headers == {}

    @staticmethod
    def test_create_uses_explicit_correlation_id() -> None:
        explicit_id = uuid4()

        envelope = EnvelopeFactory.create(SampleMessage(), correlation_id=explicit_id)

        assert envelope.correlation_id == explicit_id

    @staticmethod
    def test_create_uses_explicit_causation_id() -> None:
        explicit_id = uuid4()

        envelope = EnvelopeFactory.create(SampleMessage(), causation_id=explicit_id)

        assert envelope.causation_id == explicit_id

    @staticmethod
    def test_create_uses_explicit_headers() -> None:
        headers = {'x-trace': 'abc123'}

        envelope = EnvelopeFactory.create(SampleMessage(), headers=headers)

        assert envelope.headers == headers

    @staticmethod
    def test_create_uses_explicit_message_id() -> None:
        explicit_id = uuid4()

        envelope = EnvelopeFactory.create(SampleMessage(), message_id=explicit_id)

        assert envelope.message_id == explicit_id
        assert envelope.causation_id == explicit_id
