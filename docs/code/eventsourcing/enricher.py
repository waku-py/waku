from waku.eventsourcing import EventMetadata, IMetadataEnricher


class CorrelationIdEnricher(IMetadataEnricher):
    def __init__(self, correlation_id: str) -> None:
        self._correlation_id = correlation_id

    def enrich(self, metadata: EventMetadata, /) -> EventMetadata:
        return EventMetadata(
            correlation_id=self._correlation_id,
            causation_id=metadata.causation_id,
            extra=metadata.extra,
        )
