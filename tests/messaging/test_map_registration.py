from __future__ import annotations

from dataclasses import dataclass

import pytest
from typing_extensions import override

from waku.messaging.contracts.event import IEvent
from waku.messaging.contracts.pipeline import IPipelineBehavior, NextHandlerType
from waku.messaging.contracts.request import IRequest
from waku.messaging.events.handler import EventHandler
from waku.messaging.events.map import EventMap
from waku.messaging.exceptions import (
    EventHandlerAlreadyRegistered,
    PipelineBehaviorAlreadyRegistered,
    RequestHandlerAlreadyRegistered,
)
from waku.messaging.pipeline.map import PipelineBehaviorMap
from waku.messaging.requests.handler import RequestHandler
from waku.messaging.requests.map import RequestMap


@dataclass(frozen=True)
class _Response:
    value: str


class _Request(IRequest[_Response]):
    pass


class _Handler(RequestHandler[_Request, _Response]):
    @override
    async def handle(self, request: _Request, /) -> _Response:  # pragma: no cover
        return _Response(value='ok')


class _AnotherHandler(RequestHandler[_Request, _Response]):
    @override
    async def handle(self, request: _Request, /) -> _Response:  # pragma: no cover
        return _Response(value='another')


@dataclass(frozen=True)
class _Event(IEvent):
    pass


class _EventHandler(EventHandler[_Event]):
    @override
    async def handle(self, event: _Event, /) -> None:  # pragma: no cover
        pass


class _Behavior(IPipelineBehavior[_Request, _Response]):
    @override
    async def handle(  # pragma: no cover
        self,
        request: _Request,
        /,
        next_handler: NextHandlerType[_Request, _Response],
    ) -> _Response:
        return await next_handler(request)


# --- Duplicate registration ---


def test_request_map_rejects_duplicate_handler() -> None:
    m = RequestMap()
    m.bind(_Request, _Handler)  # ty: ignore[invalid-argument-type]

    with pytest.raises(RequestHandlerAlreadyRegistered, match='_Request already exists in registry'):
        m.bind(_Request, _AnotherHandler)  # ty: ignore[invalid-argument-type]


def test_event_map_rejects_duplicate_handler() -> None:
    m = EventMap()
    m.bind(_Event, [_EventHandler])

    with pytest.raises(EventHandlerAlreadyRegistered, match='_EventHandler already registered for _Event'):
        m.bind(_Event, [_EventHandler])


def test_pipeline_map_rejects_duplicate_behavior() -> None:
    m = PipelineBehaviorMap()
    m.bind(_Request, [_Behavior])  # ty: ignore[invalid-argument-type]

    with pytest.raises(PipelineBehaviorAlreadyRegistered, match='_Behavior already registered for _Request'):
        m.bind(_Request, [_Behavior])  # ty: ignore[invalid-argument-type]


# --- Merge ---


def test_request_map_merge_combines_entries() -> None:
    m1 = RequestMap()
    m1.bind(_Request, _Handler)  # ty: ignore[invalid-argument-type]

    m2 = RequestMap()
    m2.merge(m1)

    assert m2.has_handler(_Request)


def test_event_map_merge_combines_entries() -> None:
    m1 = EventMap()
    m1.bind(_Event, [_EventHandler])

    m2 = EventMap()
    m2.merge(m1)

    assert m2.has_handlers(_Event)


def test_pipeline_map_merge_combines_entries() -> None:
    m1 = PipelineBehaviorMap()
    m1.bind(_Request, [_Behavior])  # ty: ignore[invalid-argument-type]

    m2 = PipelineBehaviorMap()
    m2.merge(m1)

    assert m2.has_behaviors(_Request)


# --- Truthiness ---


def test_request_map_is_falsy_when_empty() -> None:
    assert not RequestMap()


def test_request_map_is_truthy_after_bind() -> None:
    m = RequestMap()
    m.bind(_Request, _Handler)  # ty: ignore[invalid-argument-type]
    assert m


def test_event_map_is_falsy_when_empty() -> None:
    assert not EventMap()


def test_event_map_is_truthy_after_bind() -> None:
    m = EventMap()
    m.bind(_Event, [_EventHandler])
    assert m


def test_pipeline_map_is_falsy_when_empty() -> None:
    assert not PipelineBehaviorMap()


def test_pipeline_map_is_truthy_after_bind() -> None:
    m = PipelineBehaviorMap()
    m.bind(_Request, [_Behavior])  # ty: ignore[invalid-argument-type]
    assert m
