from __future__ import annotations

from waku.eventsourcing.contracts.event import EventMetadata
from waku.messaging.context import message_context_scope
from waku.messaging.contracts.message import IMessage
from waku.messaging.enrichers import CorrelationEnricher

from tests.messaging.helpers import make_envelope


class _SampleMessage(IMessage):
    pass


class TestCorrelationEnricher:
    @staticmethod
    def test_enriches_metadata_with_correlation_ids() -> None:
        envelope = make_envelope(_SampleMessage())
        with message_context_scope(envelope):
            enricher = CorrelationEnricher()
            result = enricher.enrich(EventMetadata())

            assert result.correlation_id == str(envelope.correlation_id)
            assert result.causation_id == str(envelope.message_id)

    @staticmethod
    def test_preserves_existing_extra_metadata() -> None:
        envelope = make_envelope(_SampleMessage())
        with message_context_scope(envelope):
            enricher = CorrelationEnricher()
            original = EventMetadata(extra={'tenant': 'acme'})
            result = enricher.enrich(original)

            assert result.extra == {'tenant': 'acme'}
            assert result.correlation_id == str(envelope.correlation_id)
