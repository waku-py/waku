from __future__ import annotations

from typing import TYPE_CHECKING, Any, overload

from typing_extensions import override

from waku.di import AsyncContainer  # noqa: TC001  # Dishka needs runtime access
from waku.messaging.context import (
    MessageContext,
    reset_message_context,
    set_message_context,
    try_get_message_context,
)
from waku.messaging.contracts.factory import EnvelopeFactory  # noqa: TC001  # Dishka needs runtime access
from waku.messaging.dispatcher import MessageDispatcher  # noqa: TC001  # Dishka needs runtime access
from waku.messaging.interfaces import IMessageBus
from waku.messaging.router import MessageRouter  # noqa: TC001  # Dishka needs runtime access

if TYPE_CHECKING:
    from contextvars import Token

    from waku.messaging.contracts.envelope import MessageEnvelope
    from waku.messaging.contracts.event import IEvent
    from waku.messaging.contracts.message import ResponseT
    from waku.messaging.contracts.request import IRequest


class MessageBus(IMessageBus):
    __slots__ = ('_container', '_dispatcher', '_envelope_factory', '_router')

    def __init__(
        self,
        container: AsyncContainer,
        dispatcher: MessageDispatcher,
        envelope_factory: EnvelopeFactory,
        router: MessageRouter,
    ) -> None:
        self._container = container
        self._dispatcher = dispatcher
        self._envelope_factory = envelope_factory
        self._router = router

    @overload
    async def invoke(self, request: IRequest[None], /) -> None: ...

    @overload
    async def invoke(self, request: IRequest[ResponseT], /) -> ResponseT: ...

    @override
    async def invoke(self, request: IRequest[Any], /) -> Any:
        envelope = self._create_envelope(request)
        token = self._set_context(envelope)
        try:
            return await self._dispatcher.invoke_request(request)
        finally:
            self._reset_context(token)

    @override
    async def send(self, request: IRequest[Any], /) -> None:
        envelope = self._create_envelope(request)
        endpoints = self._router.resolve(type(request))
        if not endpoints:
            token = self._set_context(envelope)
            try:
                await self._dispatcher.invoke_request(request)
            finally:
                self._reset_context(token)
        else:
            for endpoint in endpoints:
                await endpoint.dispatch(envelope, self._container)

    @override
    async def publish(self, event: IEvent, /) -> None:
        envelope = self._create_envelope(event)
        endpoints = self._router.resolve(type(event))
        if not endpoints:
            token = self._set_context(envelope)
            try:
                await self._dispatcher.publish_event(event)
            finally:
                self._reset_context(token)
        else:
            excluded = self._router.routed_handler_types(type(event))
            token = self._set_context(envelope)
            try:
                await self._dispatcher.publish_event_excluding(event, exclude=excluded)
            finally:
                self._reset_context(token)
            for endpoint in endpoints:
                await endpoint.dispatch(envelope, self._container)

    def _create_envelope(self, message: Any) -> MessageEnvelope[Any]:
        ctx = try_get_message_context()
        if ctx is not None:
            return self._envelope_factory.create(
                message,
                correlation_id=ctx.correlation_id,
                causation_id=ctx.message_id,
            )
        return self._envelope_factory.create(message)

    @staticmethod
    def _set_context(envelope: MessageEnvelope[Any]) -> Token[MessageContext | None]:
        ctx = MessageContext(
            correlation_id=envelope.correlation_id,
            causation_id=envelope.causation_id,
            message_id=envelope.message_id,
            headers=envelope.headers,
        )
        return set_message_context(ctx)

    @staticmethod
    def _reset_context(token: Token[MessageContext | None]) -> None:
        reset_message_context(token)
