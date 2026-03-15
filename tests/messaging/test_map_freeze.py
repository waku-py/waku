from __future__ import annotations

from dataclasses import dataclass

import pytest
from typing_extensions import override

from waku.messaging.contracts.event import IEvent
from waku.messaging.contracts.pipeline import CallNext, IPipelineBehavior
from waku.messaging.contracts.request import IRequest
from waku.messaging.events.handler import EventHandler
from waku.messaging.events.map import EventMap
from waku.messaging.exceptions import MapFrozenError
from waku.messaging.pipeline.map import PipelineBehaviorMap, PipelineBehaviorMapEntry
from waku.messaging.requests.handler import RequestHandler
from waku.messaging.requests.map import RequestMap


@dataclass(frozen=True)
class DummyResponse:
    value: str


class DummyRequest(IRequest[DummyResponse]):
    pass


class DummyHandler(RequestHandler[DummyRequest, DummyResponse]):
    @override
    async def handle(self, request: DummyRequest, /) -> DummyResponse:  # pragma: no cover
        return DummyResponse(value='ok')


@dataclass(frozen=True)
class DummyEvent(IEvent):
    pass


class DummyEventHandler(EventHandler[DummyEvent]):
    @override
    async def handle(self, event: DummyEvent, /) -> None:  # pragma: no cover
        pass


class DummyBehavior(IPipelineBehavior[DummyRequest, DummyResponse]):
    @override
    async def handle(
        self,
        message: DummyRequest,
        /,
        call_next: CallNext[DummyResponse],
    ) -> DummyResponse:
        return await call_next()  # pragma: no cover


def _bind_request(m: RequestMap) -> None:
    m.bind(DummyRequest, DummyHandler)  # ty: ignore[invalid-argument-type]


def _bind_behavior(m: PipelineBehaviorMap) -> None:
    m.bind(PipelineBehaviorMapEntry.for_request(DummyRequest), [DummyBehavior])


def test_request_map_bind_after_freeze_raises() -> None:
    m = RequestMap()
    m.freeze()

    with pytest.raises(MapFrozenError):
        _bind_request(m)


def test_request_map_merge_after_freeze_raises() -> None:
    m = RequestMap()
    m.freeze()

    with pytest.raises(MapFrozenError):
        m.merge(RequestMap())


def test_request_map_freeze_does_not_affect_reads() -> None:
    m = RequestMap()
    _bind_request(m)
    m.freeze()

    assert m.has_handler(DummyRequest)
    assert m.get_handler_type(DummyRequest) is not None


def test_request_map_is_frozen_reflects_state() -> None:
    m = RequestMap()
    assert m.is_frozen is False
    m.freeze()
    assert m.is_frozen is True


def test_event_map_bind_after_freeze_raises() -> None:
    m = EventMap()
    m.freeze()

    with pytest.raises(MapFrozenError):
        m.bind(DummyEvent, [DummyEventHandler])


def test_event_map_merge_after_freeze_raises() -> None:
    m = EventMap()
    m.freeze()

    with pytest.raises(MapFrozenError):
        m.merge(EventMap())


def test_event_map_freeze_does_not_affect_reads() -> None:
    m = EventMap()
    m.bind(DummyEvent, [DummyEventHandler])
    m.freeze()

    assert m.has_handlers(DummyEvent)
    assert m.get_handler_type(DummyEvent) is not None


def test_event_map_is_frozen_reflects_state() -> None:
    m = EventMap()
    assert m.is_frozen is False
    m.freeze()
    assert m.is_frozen is True


def test_pipeline_map_bind_after_freeze_raises() -> None:
    m = PipelineBehaviorMap()
    m.freeze()

    with pytest.raises(MapFrozenError):
        _bind_behavior(m)


def test_pipeline_map_merge_after_freeze_raises() -> None:
    m = PipelineBehaviorMap()
    m.freeze()

    with pytest.raises(MapFrozenError):
        m.merge(PipelineBehaviorMap())


def test_pipeline_map_freeze_does_not_affect_reads() -> None:
    m = PipelineBehaviorMap()
    _bind_behavior(m)
    m.freeze()

    assert m.has_behaviors(DummyRequest)
    assert m.get_lookup_type(DummyRequest) is not None


def test_pipeline_map_is_frozen_reflects_state() -> None:
    m = PipelineBehaviorMap()
    assert m.is_frozen is False
    m.freeze()
    assert m.is_frozen is True
