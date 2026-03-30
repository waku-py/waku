from __future__ import annotations

from dataclasses import dataclass

import pytest
from typing_extensions import override

from waku.messaging.contracts.event import IEvent
from waku.messaging.contracts.pipeline import CallNext, IPipelineBehavior
from waku.messaging.contracts.request import IRequest
from waku.messaging.exceptions import (
    HandlerAlreadyRegistered,
    MapFrozenError,
    PipelineBehaviorAlreadyRegistered,
)
from waku.messaging.handler import EventHandler, RequestHandler
from waku.messaging.handler_map import HandlerMap
from waku.messaging.pipeline.map import PipelineBehaviorMap, PipelineBehaviorMapEntry


@dataclass(frozen=True)
class _Response:
    value: str


@dataclass(frozen=True)
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


class _EventBehavior(IPipelineBehavior[_Event, None]):
    @override
    async def handle(  # pragma: no cover
        self,
        message: _Event,
        /,
        call_next: CallNext[None],
    ) -> None:
        await call_next()


class _Behavior(IPipelineBehavior[_Request, _Response]):
    @override
    async def handle(  # pragma: no cover
        self,
        message: _Request,
        /,
        call_next: CallNext[_Response],
    ) -> _Response:
        return await call_next()


# --- Duplicate registration ---


def test_handler_map_rejects_duplicate_handler() -> None:
    m = HandlerMap()
    m.bind(_Request, _Handler)
    with pytest.raises(HandlerAlreadyRegistered, match='_Handler already registered for _Request'):
        m.bind(_Request, _Handler)


def test_handler_map_allows_multiple_handlers_for_events() -> None:
    m = HandlerMap()
    m.bind(_Event, _EventHandler)
    m.bind(_Event, _Handler)
    assert len(m.get_handler_types(_Event)) == 2


def test_handler_map_rejects_same_handler_twice_for_event() -> None:
    m = HandlerMap()
    m.bind(_Event, _EventHandler)
    with pytest.raises(HandlerAlreadyRegistered, match='_EventHandler already registered for _Event'):
        m.bind(_Event, _EventHandler)


def test_pipeline_map_rejects_duplicate_behavior() -> None:
    m = PipelineBehaviorMap()
    m.bind(PipelineBehaviorMapEntry.for_message(_Request), [_Behavior])

    with pytest.raises(PipelineBehaviorAlreadyRegistered, match='_Behavior already registered for _Request'):
        m.bind(PipelineBehaviorMapEntry.for_message(_Request), [_Behavior])


def test_pipeline_map_rejects_duplicate_event_behavior() -> None:
    m = PipelineBehaviorMap()
    m.bind(PipelineBehaviorMapEntry.for_message(_Event), [_EventBehavior])

    with pytest.raises(PipelineBehaviorAlreadyRegistered, match='_EventBehavior already registered for _Event'):
        m.bind(PipelineBehaviorMapEntry.for_message(_Event), [_EventBehavior])


# --- Merge ---


def test_handler_map_merge_combines_entries() -> None:
    m1 = HandlerMap()
    m1.bind(_Request, _Handler)
    m2 = HandlerMap()
    m2.merge(m1)

    handlers = m2.get_handler_types(_Request)
    assert len(handlers) == 1
    assert handlers[0] is _Handler


def test_handler_map_merge_combines_event_entries() -> None:
    m1 = HandlerMap()
    m1.bind(_Event, _EventHandler)

    m2 = HandlerMap()
    m2.merge(m1)

    handlers = m2.get_handler_types(_Event)
    assert len(handlers) == 1
    assert handlers[0] is _EventHandler


def test_pipeline_map_merge_combines_entries() -> None:
    m1 = PipelineBehaviorMap()
    m1.bind(PipelineBehaviorMapEntry.for_message(_Request), [_Behavior])

    m2 = PipelineBehaviorMap()
    m2.merge(m1)

    assert m2.has_behaviors(_Request)


def test_pipeline_map_merge_appends_to_existing_entry() -> None:
    m1 = PipelineBehaviorMap()
    m1.bind(PipelineBehaviorMapEntry.for_message(_Event), [_EventBehavior])

    m2 = PipelineBehaviorMap()
    m2.bind(PipelineBehaviorMapEntry.for_message(_Event), [_Behavior])
    m2.merge(m1)

    assert len(m2.get_behavior_types(_Event)) == 2


# --- Edge cases ---


def test_handler_map_get_handler_types_returns_empty_for_unknown() -> None:
    m = HandlerMap()
    assert m.get_handler_types(_Event) == ()


# --- Frozen map guards ---


def test_handler_map_bind_after_freeze_raises_map_frozen_error() -> None:
    m = HandlerMap()
    m.freeze()
    with pytest.raises(MapFrozenError, match='Cannot modify map after it is frozen'):
        m.bind(_Request, _Handler)


def test_handler_map_merge_after_freeze_raises_map_frozen_error() -> None:
    m1 = HandlerMap()
    m1.bind(_Request, _Handler)

    m2 = HandlerMap()
    m2.freeze()
    with pytest.raises(MapFrozenError, match='Cannot modify map after it is frozen'):
        m2.merge(m1)


def test_pipeline_map_bind_after_freeze_raises_map_frozen_error() -> None:
    m = PipelineBehaviorMap()
    m.freeze()
    with pytest.raises(MapFrozenError, match='Cannot modify map after it is frozen'):
        m.bind(PipelineBehaviorMapEntry.for_message(_Request), [_Behavior])


def test_pipeline_map_merge_after_freeze_raises_map_frozen_error() -> None:
    m1 = PipelineBehaviorMap()
    m1.bind(PipelineBehaviorMapEntry.for_message(_Request), [_Behavior])

    m2 = PipelineBehaviorMap()
    m2.freeze()
    with pytest.raises(MapFrozenError, match='Cannot modify map after it is frozen'):
        m2.merge(m1)


def test_pipeline_map_is_frozen_reflects_state() -> None:
    m = PipelineBehaviorMap()
    assert not m.is_frozen
    m.freeze()
    assert m.is_frozen
