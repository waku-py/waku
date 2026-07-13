from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Final, TypeVar, overload

from typing_extensions import override

from waku._internal.clock import Now, utc_now  # Now stays runtime: dishka introspects __init__
from waku.di import AsyncContainer  # noqa: TC001
from waku.messages import IEvent
from waku.messaging._internal.dispatch import IEndpointDispatch
from waku.messaging._internal.dispatcher import MessageDispatcher  # noqa: TC001
from waku.messaging._internal.envelope_factory import EnvelopeFactory  # noqa: TC001
from waku.messaging.context import message_context_scope, try_get_message_context
from waku.messaging.delivery import DeliveryOptions
from waku.messaging.exceptions import (
    ConflictingDeliveryOptionsError,
    DeliveryOptionNotApplicableError,
    NoRouteError,
    SchedulingNotSupportedError,
)
from waku.messaging.interfaces import IMessageBus
from waku.messaging.router import MessageRouter  # noqa: TC001

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime, timedelta

    from waku.messages import IMessage
    from waku.messaging.contracts.envelope import MessageEnvelope
    from waku.messaging.contracts.message import ResponseT
    from waku.messaging.contracts.request import IRequest
    from waku.messaging.endpoints.base import Endpoint

logger = logging.getLogger(__name__)

_ValueT = TypeVar('_ValueT')

_EMPTY_OPTIONS: Final[DeliveryOptions] = DeliveryOptions()


def _override(option_value: _ValueT | None, ctx_value: _ValueT | None) -> _ValueT | None:
    return option_value if option_value is not None else ctx_value


def _reject_non_invoke_options(options: DeliveryOptions) -> None:
    # invoke is always inline; scheduling/expiration are category errors here.
    for name, value in (
        ('scheduled_time', options.scheduled_time),
        ('schedule_delay', options.schedule_delay),
        ('deliver_by', options.deliver_by),
        ('deliver_within', options.deliver_within),
    ):
        if value is not None:
            raise DeliveryOptionNotApplicableError(name, 'invoke')


def _reject_unschedulable(envelope: MessageEnvelope[Any], endpoints: Sequence[Endpoint]) -> None:
    # Scheduled delivery is durable-local only; routing one elsewhere is fail-loud (no silent deliver-now).
    if envelope.scheduled_time is None:
        return
    for endpoint in endpoints:
        if not endpoint.supports_scheduling:
            raise SchedulingNotSupportedError(endpoint.uri)


class MessageBus(IMessageBus, IEndpointDispatch):
    __slots__ = ('_container', '_dispatcher', '_envelope_factory', '_now', '_router')

    def __init__(
        self,
        container: AsyncContainer,
        dispatcher: MessageDispatcher,
        envelope_factory: EnvelopeFactory,
        router: MessageRouter,
        now: Now = utc_now,
    ) -> None:
        self._container = container
        self._dispatcher = dispatcher
        self._envelope_factory = envelope_factory
        self._router = router
        self._now = now

    @overload
    async def invoke(self, event: IEvent, /, options: DeliveryOptions | None = None) -> None: ...

    @overload
    async def invoke(self, request: IRequest[None], /, options: DeliveryOptions | None = None) -> None: ...

    @overload
    async def invoke(self, request: IRequest[ResponseT], /, options: DeliveryOptions | None = None) -> ResponseT: ...

    @override
    async def invoke(self, message: IRequest[Any] | IEvent, /, options: DeliveryOptions | None = None) -> Any:
        if options is not None:
            _reject_non_invoke_options(options)
        envelope = self._create_envelope(message, options)
        with message_context_scope(envelope):
            if isinstance(message, IEvent):
                return await self._dispatcher.invoke_event(self._container, envelope)
            return await self._dispatcher.invoke_request(self._container, envelope)

    @override
    async def send(self, message: IMessage, /, options: DeliveryOptions | None = None) -> None:
        envelope = self._create_envelope(message, options)
        if self._is_expired(envelope):  # drop before route resolution: an expired message never raises NoRoute
            logger.info('Dropping expired message_id=%s before dispatch', envelope.message_id)
            return
        endpoints = self._router.resolve(type(message))
        if not endpoints:
            raise NoRouteError(type(message))
        _reject_unschedulable(envelope, endpoints)
        for endpoint in endpoints:
            await endpoint.dispatch(envelope, self._container)

    @override
    async def publish(self, message: IMessage, /, options: DeliveryOptions | None = None) -> None:
        envelope = self._create_envelope(message, options)
        if self._is_expired(envelope):
            logger.info('Dropping expired message_id=%s before dispatch', envelope.message_id)
            return
        endpoints = self._router.resolve(type(message))
        _reject_unschedulable(envelope, endpoints)  # fan-out is fail-loud: ANY non-durable subscriber raises
        for endpoint in endpoints:
            await endpoint.dispatch(envelope, self._container)

    @override
    async def dispatch_to(self, message: IMessage, endpoints: Sequence[Endpoint]) -> None:
        # Destination-dispatch seam for the cascading behaviors: NO route resolution — the caller
        # already partitioned the endpoint set — but the same context-propagated envelope as send/publish.
        if not endpoints:
            return
        envelope = self._create_envelope(message)
        for endpoint in endpoints:
            await endpoint.dispatch(envelope, self._container)

    def _is_expired(self, envelope: MessageEnvelope[Any]) -> bool:
        return envelope.expires_at is not None and envelope.expires_at <= self._now()

    @override
    async def schedule_send(
        self,
        message: IMessage,
        /,
        *,
        at: datetime | None = None,
        delay: timedelta | None = None,
    ) -> None:
        # Both-set falls through to DeliveryOptions.__post_init__ (canonical message); guard the neither-case.
        if at is None and delay is None:
            msg = 'schedule_send requires exactly one of at or delay'
            raise ConflictingDeliveryOptionsError(msg)
        await self.send(message, DeliveryOptions(scheduled_time=at, schedule_delay=delay))

    @override
    async def schedule_publish(
        self,
        message: IMessage,
        /,
        *,
        at: datetime | None = None,
        delay: timedelta | None = None,
    ) -> None:
        # Both-set falls through to DeliveryOptions.__post_init__ (canonical message); guard the neither-case.
        if at is None and delay is None:
            msg = 'schedule_publish requires exactly one of at or delay'
            raise ConflictingDeliveryOptionsError(msg)
        await self.publish(message, DeliveryOptions(scheduled_time=at, schedule_delay=delay))

    def _create_envelope(self, message: IMessage, options: DeliveryOptions | None = None) -> MessageEnvelope[Any]:
        ctx = try_get_message_context()
        opt = options or _EMPTY_OPTIONS
        ctx_headers = ctx.headers if ctx is not None else {}
        return self._envelope_factory.create(
            message,
            correlation_id=_override(opt.correlation_id, ctx.correlation_id if ctx else None),
            causation_id=_override(opt.causation_id, str(ctx.message_id) if ctx else None),
            group_id=_override(opt.group_id, ctx.group_id if ctx else None),
            tenant_id=_override(opt.tenant_id, ctx.tenant_id if ctx else None),
            headers={**ctx_headers, **(opt.headers or {})},  # fresh dict; never alias the caller's mapping
            scheduled_time=self._resolve_scheduled(opt),
            expires_at=self._resolve_expires(opt),
        )

    def _resolve_scheduled(self, options: DeliveryOptions) -> datetime | None:
        if options.scheduled_time is not None:
            return options.scheduled_time
        if options.schedule_delay is not None:
            return self._now() + options.schedule_delay
        return None

    def _resolve_expires(self, options: DeliveryOptions) -> datetime | None:
        if options.deliver_by is not None:
            return options.deliver_by
        if options.deliver_within is not None:
            return self._now() + options.deliver_within
        return None
