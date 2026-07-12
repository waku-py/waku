from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypeVar

import pytest
from typing_extensions import override

from waku.exceptions import ImproperlyConfiguredError
from waku.messages import IEvent
from waku.messaging import HandlerMap
from waku.messaging.contracts.request import IRequest
from waku.messaging.exceptions import HandlerAlreadyRegisteredError, MapFrozenError
from waku.messaging.handler import EventHandler, MessageHandler, RequestHandler
from waku.messaging.modules import MessagingExtension


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


@dataclass(frozen=True)
class _Event(IEvent):
    pass


class _EventHandler(EventHandler[_Event]):
    @override
    async def handle(self, event: _Event, /) -> None:  # pragma: no cover
        pass


# --- Duplicate registration ---


def test_handler_map_rejects_duplicate_handler() -> None:
    m = HandlerMap()
    m.bind(_Request, _Handler)
    with pytest.raises(HandlerAlreadyRegisteredError, match='_Handler already registered for _Request'):
        m.bind(_Request, _Handler)


def test_handler_map_allows_multiple_handlers_for_events() -> None:
    m = HandlerMap()
    m.bind(_Event, _EventHandler)
    m.bind(_Event, _Handler)
    assert len(m.get_handler_types(_Event)) == 2


def test_handler_map_rejects_same_handler_twice_for_event() -> None:
    m = HandlerMap()
    m.bind(_Event, _EventHandler)
    with pytest.raises(HandlerAlreadyRegisteredError, match='_EventHandler already registered for _Event'):
        m.bind(_Event, _EventHandler)


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


# --- Edge cases ---


def test_handler_map_get_handler_types_returns_empty_for_unknown() -> None:
    m = HandlerMap()
    assert m.get_handler_types(_Event) == ()


def test_handler_map_get_handler_types_returns_independent_snapshot() -> None:
    m = HandlerMap()
    m.bind(_Event, _EventHandler)
    snapshot = m.get_handler_types(_Event)
    m.bind(_Event, _Handler)
    assert list(snapshot) == [_EventHandler]


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


# --- bind() message-type inference ---


class TestBindInfersMessageType:
    @staticmethod
    def test_bind_infers_request_message_type_from_handler() -> None:
        hm = MessagingExtension().bind(_Handler).handler_map
        assert hm.get_handler_types(_Request) == (_Handler,)

    @staticmethod
    def test_bind_infers_event_message_type_from_handler() -> None:
        hm = MessagingExtension().bind(_EventHandler).handler_map
        assert hm.get_handler_types(_Event) == (_EventHandler,)

    @staticmethod
    def test_bind_varargs_infers_each_handler_for_mixed_types() -> None:
        hm = MessagingExtension().bind(_Handler, _EventHandler).handler_map
        assert hm.get_handler_types(_Request) == (_Handler,)
        assert hm.get_handler_types(_Event) == (_EventHandler,)

    @staticmethod
    def test_bind_explicit_two_arg_form_still_works() -> None:
        hm = MessagingExtension().bind(_Request, _Handler).handler_map
        assert hm.get_handler_types(_Request) == (_Handler,)

    @staticmethod
    def test_bind_explicit_escape_binds_handler_beyond_its_generic() -> None:
        hm = MessagingExtension().bind(_Event, _Handler).handler_map
        assert hm.get_handler_types(_Event) == (_Handler,)

    @staticmethod
    def test_bind_inference_resolves_through_indirect_subclass() -> None:
        class _SubHandler(_Handler):
            pass

        hm = MessagingExtension().bind(_SubHandler).handler_map
        assert hm.get_handler_types(_Request) == (_SubHandler,)

    @staticmethod
    def test_bind_raises_when_message_type_cannot_be_inferred() -> None:
        T = TypeVar('T', bound=IRequest[Any])

        class _GenericHandler(RequestHandler[T, Any]):
            @override
            async def handle(self, request: T, /) -> Any:  # pragma: no cover
                ...

        with pytest.raises(ImproperlyConfiguredError, match='Cannot infer message type'):
            MessagingExtension().bind(_GenericHandler)

    @staticmethod
    def test_bind_explicit_form_without_handler_raises() -> None:
        with pytest.raises(ImproperlyConfiguredError, match='at least one handler'):
            MessagingExtension().bind(_Request)  # type: ignore[arg-type]

    @staticmethod
    def test_bind_raises_when_message_type_is_any() -> None:
        class _AnyHandler(MessageHandler[Any, Any]):
            @override
            async def handle(self, message: Any, /) -> Any:  # pragma: no cover
                ...

        with pytest.raises(ImproperlyConfiguredError, match='Cannot infer message type'):
            MessagingExtension().bind(_AnyHandler)

    @staticmethod
    def test_bind_rejects_non_class_first_argument() -> None:
        with pytest.raises(ImproperlyConfiguredError, match='orders'):
            MessagingExtension().bind('orders', _Handler)  # type: ignore[call-overload]

    @staticmethod
    def test_bind_rejects_non_message_first_argument() -> None:
        class _NotAMessage:
            pass

        with pytest.raises(ImproperlyConfiguredError, match='_NotAMessage'):
            MessagingExtension().bind(_NotAMessage, _Handler)  # type: ignore[arg-type]
