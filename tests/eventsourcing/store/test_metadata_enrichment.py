from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

from typing_extensions import override

from waku.eventsourcing.contracts.event import EventEnvelope, EventMetadata, IMetadataEnricher
from waku.eventsourcing.contracts.stream import NoStream, StreamId

from tests.eventsourcing.store.domain import OrderCreated, make_envelope

if TYPE_CHECKING:
    from tests.eventsourcing.store.conftest import EventStoreFactory


class CorrelationIdEnricher(IMetadataEnricher):
    @override
    def enrich(self, metadata: EventMetadata, /) -> EventMetadata:
        return dataclasses.replace(metadata, extra={**metadata.extra, 'enriched_by': 'correlation'})


class TimestampEnricher(IMetadataEnricher):
    @override
    def enrich(self, metadata: EventMetadata, /) -> EventMetadata:
        return dataclasses.replace(metadata, extra={**metadata.extra, 'timestamp_added': True})


class SourceEnricher(IMetadataEnricher):
    @override
    def enrich(self, metadata: EventMetadata, /) -> EventMetadata:
        return dataclasses.replace(metadata, extra={**metadata.extra, 'source': 'test-service'})


async def test_single_enricher_adds_extra_metadata_fields(
    store_factory: EventStoreFactory,
    stream_id: StreamId,
) -> None:
    store = store_factory(enrichers=[SourceEnricher()])
    await store.append_to_stream(
        stream_id,
        [make_envelope(OrderCreated(order_id='123'))],
        expected_version=NoStream(),
    )

    events = await store.read_stream(stream_id)

    assert events[0].metadata.extra == {'source': 'test-service'}


async def test_chained_enrichers_apply_sequentially(
    store_factory: EventStoreFactory,
    stream_id: StreamId,
) -> None:
    store = store_factory(enrichers=[CorrelationIdEnricher(), TimestampEnricher()])
    await store.append_to_stream(
        stream_id,
        [make_envelope(OrderCreated(order_id='123'))],
        expected_version=NoStream(),
    )

    events = await store.read_stream(stream_id)

    assert events[0].metadata.extra == {'enriched_by': 'correlation', 'timestamp_added': True}


async def test_enricher_preserves_existing_envelope_metadata(
    store_factory: EventStoreFactory,
    stream_id: StreamId,
) -> None:
    store = store_factory(enrichers=[SourceEnricher()])
    envelope = EventEnvelope(
        domain_event=OrderCreated(order_id='123'),
        metadata=EventMetadata(correlation_id='existing-corr-id'),
    )
    await store.append_to_stream(
        stream_id,
        [envelope],
        expected_version=NoStream(),
    )

    events = await store.read_stream(stream_id)

    assert events[0].metadata.correlation_id == 'existing-corr-id'
    assert events[0].metadata.extra == {'source': 'test-service'}


async def test_no_enrichers_leaves_metadata_unchanged(
    store_factory: EventStoreFactory,
    stream_id: StreamId,
) -> None:
    store = store_factory(enrichers=[])
    await store.append_to_stream(
        stream_id,
        [make_envelope(OrderCreated(order_id='123'))],
        expected_version=NoStream(),
    )

    events = await store.read_stream(stream_id)

    assert events[0].metadata == EventMetadata()
