from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from waku.eventsourcing.contracts.event import EventMetadata, IMetadataEnricher

__all__ = ['enrich_metadata']


def enrich_metadata(
    metadata: EventMetadata,
    enrichers: Sequence[IMetadataEnricher],
) -> EventMetadata:
    """Apply each enricher to the event metadata in order.

    Args:
        metadata: The base event metadata.
        enrichers: Enrichers applied left to right.

    Returns:
        The metadata after all enrichers have run.
    """
    for enricher in enrichers:
        metadata = enricher.enrich(metadata)
    return metadata
