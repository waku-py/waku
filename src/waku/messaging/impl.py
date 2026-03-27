from __future__ import annotations

from typing import TYPE_CHECKING, Any, overload

from typing_extensions import override

from waku.di import AsyncContainer  # noqa: TC001
from waku.messaging.context import message_context_scope, try_get_message_context
from waku.messaging.contracts.factory import EnvelopeFactory  # noqa: TC001
from waku.messaging.dispatcher import MessageDispatcher  # noqa: TC001
from waku.messaging.exceptions import NoRouteError
from waku.messaging.interfaces import IMessageBus
from waku.messaging.router import MessageRouter  # noqa: TC001

if TYPE_CHECKING:
    from waku.messaging.contracts.envelope import MessageEnvelope
    from waku.messaging.contracts.message import IMessage, ResponseT
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
        with message_context_scope(envelope):
            return await self._dispatcher.invoke_request(request)

    @override
    async def send(self, message: IMessage, /) -> None:
        envelope = self._create_envelope(message)
        endpoints = self._router.resolve(type(message))
        if not endpoints:
            raise NoRouteError(type(message))
        for endpoint in endpoints:
            await endpoint.dispatch(envelope, self._container)

    @override
    async def publish(self, message: IMessage, /) -> None:
        envelope = self._create_envelope(message)
        for endpoint in self._router.resolve(type(message)):
            await endpoint.dispatch(envelope, self._container)

    def _create_envelope(self, message: IMessage) -> MessageEnvelope[Any]:
        ctx = try_get_message_context()
        if ctx is not None:
            return self._envelope_factory.create(
                message,
                correlation_id=ctx.correlation_id,
                causation_id=ctx.message_id,
            )
        return self._envelope_factory.create(message)
