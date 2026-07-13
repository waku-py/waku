from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from waku.messages import IMessage
from waku.messaging._internal.envelope_factory import EnvelopeFactory
from waku.messaging._internal.identity import MessageTypeRegistry


class SampleMessage(IMessage):
    pass


def _make_factory(*, identity: str | None = None) -> EnvelopeFactory:
    identities: dict[type[IMessage], str] = {SampleMessage: identity} if identity is not None else {}
    registry = MessageTypeRegistry(identities=identities, known_types=[SampleMessage])
    return EnvelopeFactory(registry=registry)


class TestEnvelopeFactory:
    @staticmethod
    def test_create_generates_uuid_message_id() -> None:
        envelope = _make_factory().create(SampleMessage())

        assert isinstance(envelope.message_id, UUID)

    @staticmethod
    def test_create_generates_correlation_id_when_not_provided() -> None:
        envelope = _make_factory().create(SampleMessage())

        assert isinstance(envelope.correlation_id, str)
        assert UUID(envelope.correlation_id)  # UUID-shaped by default
        assert envelope.correlation_id != str(envelope.message_id)

    @staticmethod
    def test_create_sets_causation_id_to_message_id_when_not_provided() -> None:
        envelope = _make_factory().create(SampleMessage())

        assert envelope.causation_id == str(envelope.message_id)

    @staticmethod
    def test_create_uses_registry_fqn_fallback() -> None:
        envelope = _make_factory().create(SampleMessage())

        expected = f'{SampleMessage.__module__}.{SampleMessage.__qualname__}'
        assert envelope.message_type == expected

    @staticmethod
    def test_create_uses_explicit_alias_from_registry() -> None:
        envelope = _make_factory(identity='sample').create(SampleMessage())

        assert envelope.message_type == 'sample'

    @staticmethod
    def test_create_sets_message_version_from_registry() -> None:
        envelope = _make_factory().create(SampleMessage())

        assert envelope.message_version == 1

    @staticmethod
    def test_create_sets_utc_timestamp() -> None:
        before = datetime.now(tz=UTC)
        envelope = _make_factory().create(SampleMessage())
        after = datetime.now(tz=UTC)

        assert before <= envelope.timestamp <= after

    @staticmethod
    def test_create_stamps_timestamp_from_injected_clock() -> None:
        fixed = datetime(2026, 6, 21, tzinfo=UTC)
        registry = MessageTypeRegistry(identities={}, known_types=[SampleMessage])
        factory = EnvelopeFactory(registry=registry, now=lambda: fixed)

        envelope = factory.create(SampleMessage())

        assert envelope.timestamp == fixed

    @staticmethod
    def test_create_stores_message_as_payload() -> None:
        message = SampleMessage()

        envelope = _make_factory().create(message)

        assert envelope.payload is message

    @staticmethod
    def test_create_uses_empty_dict_for_headers_when_not_provided() -> None:
        envelope = _make_factory().create(SampleMessage())

        assert envelope.headers == {}

    @staticmethod
    def test_create_uses_explicit_correlation_id() -> None:
        explicit_id = str(uuid4())

        envelope = _make_factory().create(SampleMessage(), correlation_id=explicit_id)

        assert envelope.correlation_id == explicit_id

    @staticmethod
    def test_create_uses_explicit_causation_id() -> None:
        explicit_id = str(uuid4())

        envelope = _make_factory().create(SampleMessage(), causation_id=explicit_id)

        assert envelope.causation_id == explicit_id

    @staticmethod
    def test_create_uses_explicit_headers() -> None:
        headers = {'x-trace': 'abc123'}

        envelope = _make_factory().create(SampleMessage(), headers=headers)

        assert envelope.headers == headers

    @staticmethod
    def test_create_uses_explicit_message_id() -> None:
        explicit_id = uuid4()

        envelope = _make_factory().create(SampleMessage(), message_id=explicit_id)

        assert envelope.message_id == explicit_id
        assert envelope.causation_id == str(explicit_id)

    @staticmethod
    def test_create_forwards_group_id() -> None:
        envelope = _make_factory().create(SampleMessage(), group_id='order-42')

        assert envelope.group_id == 'order-42'

    @staticmethod
    def test_create_defaults_group_id_to_none() -> None:
        envelope = _make_factory().create(SampleMessage())

        assert envelope.group_id is None

    @staticmethod
    def test_create_forwards_tenant_id() -> None:
        envelope = _make_factory().create(SampleMessage(), tenant_id='t1')

        assert envelope.tenant_id == 't1'

    @staticmethod
    def test_create_defaults_tenant_id_to_none() -> None:
        envelope = _make_factory().create(SampleMessage())

        assert envelope.tenant_id is None
