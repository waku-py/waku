from __future__ import annotations

from typing import Any

import pytest
from typing_extensions import override

from waku.exceptions import ImproperlyConfiguredError
from waku.messaging.endpoints.aspects import ListenAspect, SendAspect
from waku.messaging.endpoints.base import BrokerEndpointEntry, LocalQueueEntry
from waku.messaging.endpoints.merge import merge_broker_endpoints
from waku.messaging.transport.interfaces import IEnvelopeMapper


class _MapperA(IEnvelopeMapper[Any, Any]):
    @override
    def map_outgoing(self, payload: dict[str, Any], metadata: Any) -> Any:
        raise NotImplementedError  # pragma: no cover

    @override
    async def map_incoming(self, msg: Any) -> tuple[dict[str, Any], Any]:
        raise NotImplementedError  # pragma: no cover


class _MapperB(IEnvelopeMapper[Any, Any]):
    @override
    def map_outgoing(self, payload: dict[str, Any], metadata: Any) -> Any:
        raise NotImplementedError  # pragma: no cover

    @override
    async def map_incoming(self, msg: Any) -> tuple[dict[str, Any], Any]:
        raise NotImplementedError  # pragma: no cover


class TestMergeBrokerEndpointsComposition:
    @staticmethod
    def test_listen_and_send_fragments_compose_into_one_endpoint() -> None:
        entries = (
            BrokerEndpointEntry(uri='kafka://orders', listen=ListenAspect()),
            BrokerEndpointEntry(uri='kafka://orders', send=SendAspect()),
        )

        merged = merge_broker_endpoints(entries, inbox_configured=True)

        assert len(merged) == 1
        assert merged[0].uri == 'kafka://orders'
        assert merged[0].listen is not None
        assert merged[0].send is not None

    @staticmethod
    def test_send_only_endpoint_has_no_listen_aspect() -> None:
        entries = (BrokerEndpointEntry(uri='kafka://orders', send=SendAspect()),)

        merged = merge_broker_endpoints(entries, inbox_configured=False)

        assert len(merged) == 1
        assert merged[0].send is not None
        assert merged[0].listen is None


class TestMergeBrokerEndpointsConflicts:
    @staticmethod
    def test_conflicting_mappers_raise() -> None:
        mapper_a = _MapperA()
        mapper_b = _MapperB()
        entries = (
            BrokerEndpointEntry(uri='kafka://orders', send=SendAspect(), mapper=mapper_a),
            BrokerEndpointEntry(uri='kafka://orders', listen=ListenAspect(), mapper=mapper_b),
        )

        with pytest.raises(ImproperlyConfiguredError, match='conflicting envelope mappers'):
            merge_broker_endpoints(entries, inbox_configured=True)

    @staticmethod
    def test_conflicting_partition_by_raise() -> None:
        def extractor_a(message: Any) -> str | None:
            raise NotImplementedError  # pragma: no cover

        def extractor_b(message: Any) -> str | None:
            raise NotImplementedError  # pragma: no cover

        entries = (
            BrokerEndpointEntry(uri='kafka://orders', send=SendAspect(), partition_by=extractor_a),
            BrokerEndpointEntry(uri='kafka://orders', listen=ListenAspect(), partition_by=extractor_b),
        )

        with pytest.raises(ImproperlyConfiguredError, match='conflicting partition_by'):
            merge_broker_endpoints(entries, inbox_configured=True)

    @staticmethod
    def test_multiple_listen_aspects_for_same_uri_raise() -> None:
        entries = (
            BrokerEndpointEntry(uri='kafka://orders', listen=ListenAspect()),
            BrokerEndpointEntry(uri='kafka://orders', listen=ListenAspect()),
        )

        with pytest.raises(ImproperlyConfiguredError):
            merge_broker_endpoints(entries, inbox_configured=True)

    @staticmethod
    def test_multiple_send_aspects_for_same_uri_raise() -> None:
        entries = (
            BrokerEndpointEntry(uri='kafka://orders', send=SendAspect()),
            BrokerEndpointEntry(uri='kafka://orders', send=SendAspect()),
        )

        with pytest.raises(ImproperlyConfiguredError):
            merge_broker_endpoints(entries, inbox_configured=False)


class TestMergeBrokerEndpointsInheritance:
    @staticmethod
    def test_mapper_set_on_one_fragment_is_inherited_by_merged_endpoint() -> None:
        mapper = _MapperA()
        entries = (
            BrokerEndpointEntry(uri='kafka://orders', send=SendAspect(), mapper=mapper),
            BrokerEndpointEntry(uri='kafka://orders', listen=ListenAspect()),
        )

        merged = merge_broker_endpoints(entries, inbox_configured=True)

        assert merged[0].mapper is mapper

    @staticmethod
    def test_partition_by_set_on_one_fragment_is_inherited_by_merged_endpoint() -> None:
        def extractor(message: Any) -> str | None:
            raise NotImplementedError  # pragma: no cover

        entries = (
            BrokerEndpointEntry(uri='kafka://orders', send=SendAspect(), partition_by=extractor),
            BrokerEndpointEntry(uri='kafka://orders', listen=ListenAspect()),
        )

        merged = merge_broker_endpoints(entries, inbox_configured=True)

        assert merged[0].partition_by is extractor

    @staticmethod
    def test_same_mapper_instance_on_both_fragments_is_not_a_conflict() -> None:
        mapper = _MapperA()
        entries = (
            BrokerEndpointEntry(uri='kafka://orders', send=SendAspect(), mapper=mapper),
            BrokerEndpointEntry(uri='kafka://orders', listen=ListenAspect(), mapper=mapper),
        )

        merged = merge_broker_endpoints(entries, inbox_configured=True)

        assert merged[0].mapper is mapper


class TestMergeBrokerEndpointsValidation:
    @staticmethod
    def test_listen_without_inbox_configured_raises() -> None:
        entries = (BrokerEndpointEntry(uri='kafka://orders', listen=ListenAspect()),)

        with pytest.raises(ImproperlyConfiguredError, match='inbox'):
            merge_broker_endpoints(entries, inbox_configured=False)

    @staticmethod
    def test_endpoint_with_neither_listen_nor_send_raises() -> None:
        entries = (BrokerEndpointEntry(uri='kafka://orders'),)

        with pytest.raises(ImproperlyConfiguredError):
            merge_broker_endpoints(entries, inbox_configured=True)


class TestMergeBrokerEndpointsFiltering:
    @staticmethod
    def test_local_queue_entry_is_ignored() -> None:
        entries = (
            LocalQueueEntry(uri='local://commands'),
            BrokerEndpointEntry(uri='kafka://orders', send=SendAspect()),
        )

        merged = merge_broker_endpoints(entries, inbox_configured=False)

        assert len(merged) == 1
        assert merged[0].uri == 'kafka://orders'
