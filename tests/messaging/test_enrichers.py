from __future__ import annotations

from uuid import uuid4

from waku.eventsourcing.contracts.event import EventMetadata
from waku.messaging.context import MessageContext, reset_message_context, set_message_context
from waku.messaging.enrichers import CorrelationEnricher


class TestCorrelationEnricher:
    @staticmethod
    def test_enriches_metadata_with_correlation_ids() -> None:
        ctx = MessageContext(
            correlation_id=uuid4(),
            causation_id=uuid4(),
            message_id=uuid4(),
            headers={},
        )
        token = set_message_context(ctx)
        try:
            enricher = CorrelationEnricher()
            result = enricher.enrich(EventMetadata())

            assert result.correlation_id == str(ctx.correlation_id)
            assert result.causation_id == str(ctx.message_id)
        finally:
            reset_message_context(token)

    @staticmethod
    def test_preserves_existing_extra_metadata() -> None:
        ctx = MessageContext(
            correlation_id=uuid4(),
            causation_id=uuid4(),
            message_id=uuid4(),
            headers={},
        )
        token = set_message_context(ctx)
        try:
            enricher = CorrelationEnricher()
            original = EventMetadata(extra={'tenant': 'acme'})
            result = enricher.enrich(original)

            assert result.extra == {'tenant': 'acme'}
            assert result.correlation_id == str(ctx.correlation_id)
        finally:
            reset_message_context(token)
