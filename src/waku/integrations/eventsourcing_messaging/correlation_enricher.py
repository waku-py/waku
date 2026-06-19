from __future__ import annotations

import dataclasses

from typing_extensions import override

from waku.eventsourcing.contracts.event import EventMetadata, IMetadataEnricher
from waku.messaging.context import try_get_message_context

__all__ = [
    'CorrelationEnricher',
]


class CorrelationEnricher(IMetadataEnricher):
    @override
    def enrich(self, metadata: EventMetadata, /) -> EventMetadata:
        ctx = try_get_message_context()
        if ctx is None:
            return metadata
        return dataclasses.replace(
            metadata,
            correlation_id=str(ctx.correlation_id),
            causation_id=str(ctx.message_id),
        )
