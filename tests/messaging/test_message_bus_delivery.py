from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest
from typing_extensions import override

from waku.messaging import (
    DeliveryOptions,
    IEvent,
    IMessage,
    IMessageBus,
    IRequest,
    MessagingConfig,
    MessagingExtension,
    MessagingModule,
    RequestHandler,
)
from waku.messaging.context import get_message_context, message_context_scope
from waku.messaging.contracts.factory import EnvelopeFactory
from waku.messaging.dispatcher import MessageDispatcher
from waku.messaging.endpoints.base import Endpoint
from waku.messaging.exceptions import (
    ConflictingDeliveryOptionsError,
    DeliveryOptionNotApplicableError,
    SchedulingNotSupportedError,
)
from waku.messaging.impl import MessageBus
from waku.messaging.router import MessageRouter
from waku.testing import create_test_app

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    from dishka import AsyncContainer

    from waku.messaging.contracts.envelope import MessageEnvelope

_NOW = datetime(2026, 6, 21, 12, 0, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class _Note(IEvent):
    value: str = 'n'


@dataclass(frozen=True, slots=True)
class _Cmd(IRequest[str]):
    name: str


class _CorrelationEchoHandler(RequestHandler[_Cmd, str]):
    @override
    async def handle(self, request: _Cmd, /) -> str:
        return get_message_context().correlation_id


class _CapturingEndpoint(Endpoint):
    def __init__(self, uri: str = 'spy://q', *, supports_scheduling: bool = False) -> None:
        super().__init__(uri)
        self.captured: list[MessageEnvelope[Any]] = []
        self._supports_scheduling = supports_scheduling

    @property
    @override
    def supports_scheduling(self) -> bool:
        return self._supports_scheduling

    @override
    async def dispatch(self, envelope: MessageEnvelope[Any], scope: AsyncContainer) -> None:
        self.captured.append(envelope)

    @override
    async def start(self) -> None: ...

    @override
    async def stop(self) -> None: ...

    @property
    def last(self) -> MessageEnvelope[Any] | None:
        return self.captured[-1] if self.captured else None


@pytest.fixture
async def container() -> AsyncIterator[AsyncContainer]:
    async with (
        create_test_app(
            imports=[MessagingModule.register(MessagingConfig())],
            extensions=[MessagingExtension().bind(_CorrelationEchoHandler)],
        ) as app,
        app.container() as scope,
    ):
        yield scope


async def _spy_bus(
    scope: AsyncContainer,
    *,
    now: Callable[[], datetime] = lambda: _NOW,
    routes: tuple[type[IMessage], ...] = (_Note,),
    endpoint: _CapturingEndpoint | None = None,
) -> tuple[MessageBus, _CapturingEndpoint]:
    endpoint = endpoint or _CapturingEndpoint()
    dispatcher = await scope.get(MessageDispatcher)
    factory = await scope.get(EnvelopeFactory)
    router = MessageRouter(routes=dict.fromkeys(routes, (endpoint,)), endpoints=(endpoint,))
    return MessageBus(scope, dispatcher, factory, router, now=now), endpoint


async def _bus_with_endpoints(
    scope: AsyncContainer,
    endpoints: tuple[_CapturingEndpoint, ...],
    *,
    now: Callable[[], datetime] = lambda: _NOW,
) -> MessageBus:
    dispatcher = await scope.get(MessageDispatcher)
    factory = await scope.get(EnvelopeFactory)
    router = MessageRouter(routes={_Note: endpoints}, endpoints=endpoints)
    return MessageBus(scope, dispatcher, factory, router, now=now)


async def test_send_applies_correlation_and_group_overrides(container: AsyncContainer) -> None:
    bus, endpoint = await _spy_bus(container)
    cid = str(uuid4())

    await bus.send(_Note(), DeliveryOptions(correlation_id=cid, group_id='g1'))

    assert endpoint.last is not None
    assert endpoint.last.correlation_id == cid
    assert endpoint.last.group_id == 'g1'


async def test_send_applies_causation_override(container: AsyncContainer) -> None:
    bus, endpoint = await _spy_bus(container)
    causation = str(uuid4())

    await bus.send(_Note(), DeliveryOptions(causation_id=causation))

    assert endpoint.last is not None
    assert endpoint.last.causation_id == causation


async def test_option_correlation_beats_ambient_context(container: AsyncContainer) -> None:
    bus, endpoint = await _spy_bus(container)
    ambient = await _spy_bus(container)
    ambient_envelope = ambient[0]._create_envelope(_Note())  # noqa: SLF001
    option_cid = str(uuid4())

    with message_context_scope(ambient_envelope):
        await bus.send(_Note(), DeliveryOptions(correlation_id=option_cid))

    assert endpoint.last is not None
    assert endpoint.last.correlation_id == option_cid


async def test_headers_merge_is_a_fresh_dict(container: AsyncContainer) -> None:
    bus, endpoint = await _spy_bus(container)
    headers = {'a': '1'}

    await bus.send(_Note(), DeliveryOptions(headers=headers))
    headers['a'] = 'mutated'

    assert endpoint.last is not None
    assert endpoint.last.headers['a'] == '1'


async def test_ambient_and_option_headers_union_with_option_winning(container: AsyncContainer) -> None:
    bus, endpoint = await _spy_bus(container)
    ambient_envelope = (await _spy_bus(container))[0]._create_envelope(  # noqa: SLF001
        _Note(),
        DeliveryOptions(headers={'shared': 'ambient', 'ambient_only': 'a'}),
    )

    with message_context_scope(ambient_envelope):
        await bus.send(_Note(), DeliveryOptions(headers={'shared': 'option', 'option_only': 'o'}))

    assert endpoint.last is not None
    assert endpoint.last.headers == {'shared': 'option', 'ambient_only': 'a', 'option_only': 'o'}


async def test_send_resolves_scheduled_time_from_absolute(container: AsyncContainer) -> None:
    bus, endpoint = await _spy_bus(container, endpoint=_CapturingEndpoint(supports_scheduling=True))
    when = _NOW + timedelta(hours=1)

    await bus.send(_Note(), DeliveryOptions(scheduled_time=when))

    assert endpoint.last is not None
    assert endpoint.last.scheduled_time == when


async def test_send_resolves_scheduled_time_from_relative_delay(container: AsyncContainer) -> None:
    bus, endpoint = await _spy_bus(container, endpoint=_CapturingEndpoint(supports_scheduling=True))

    await bus.send(_Note(), DeliveryOptions(schedule_delay=timedelta(seconds=30)))

    assert endpoint.last is not None
    assert endpoint.last.scheduled_time == _NOW + timedelta(seconds=30)


async def test_send_resolves_expiry_from_relative_within(container: AsyncContainer) -> None:
    bus, endpoint = await _spy_bus(container)

    await bus.send(_Note(), DeliveryOptions(deliver_within=timedelta(seconds=45)))

    assert endpoint.last is not None
    assert endpoint.last.expires_at == _NOW + timedelta(seconds=45)


async def test_publish_applies_overrides(container: AsyncContainer) -> None:
    bus, endpoint = await _spy_bus(container)
    cid = str(uuid4())

    await bus.publish(_Note(), DeliveryOptions(correlation_id=cid))

    assert endpoint.last is not None
    assert endpoint.last.correlation_id == cid


@pytest.mark.parametrize(
    'options',
    [
        DeliveryOptions(scheduled_time=_NOW),
        DeliveryOptions(schedule_delay=timedelta(seconds=5)),
        DeliveryOptions(deliver_by=_NOW),
        DeliveryOptions(deliver_within=timedelta(seconds=5)),
    ],
)
async def test_invoke_rejects_scheduling_and_expiration_options(
    container: AsyncContainer,
    options: DeliveryOptions,
) -> None:
    bus = await container.get(IMessageBus)

    with pytest.raises(DeliveryOptionNotApplicableError):
        await bus.invoke(_Cmd(name='x'), options)


async def test_invoke_applies_envelope_native_override(container: AsyncContainer) -> None:
    bus = await container.get(IMessageBus)
    cid = str(uuid4())

    result = await bus.invoke(_Cmd(name='x'), DeliveryOptions(correlation_id=cid))

    assert result == cid


async def test_schedule_send_with_absolute_at_resolves_scheduled_time(container: AsyncContainer) -> None:
    bus, endpoint = await _spy_bus(container, endpoint=_CapturingEndpoint(supports_scheduling=True))
    when = _NOW + timedelta(hours=2)

    await bus.schedule_send(_Note(), at=when)

    assert endpoint.last is not None
    assert endpoint.last.scheduled_time == when


async def test_schedule_send_with_relative_delay_resolves_scheduled_time(container: AsyncContainer) -> None:
    bus, endpoint = await _spy_bus(container, endpoint=_CapturingEndpoint(supports_scheduling=True))

    await bus.schedule_send(_Note(), delay=timedelta(seconds=90))

    assert endpoint.last is not None
    assert endpoint.last.scheduled_time == _NOW + timedelta(seconds=90)


@pytest.mark.parametrize('verb', ['schedule_send', 'schedule_publish'])
async def test_schedule_without_at_or_delay_raises(container: AsyncContainer, verb: str) -> None:
    bus, _ = await _spy_bus(container)

    with pytest.raises(ConflictingDeliveryOptionsError):
        await getattr(bus, verb)(_Note())


@pytest.mark.parametrize('verb', ['schedule_send', 'schedule_publish'])
async def test_schedule_with_both_at_and_delay_raises(container: AsyncContainer, verb: str) -> None:
    bus, _ = await _spy_bus(container)

    with pytest.raises(ConflictingDeliveryOptionsError):
        await getattr(bus, verb)(_Note(), at=_NOW, delay=timedelta(seconds=5))


async def test_schedule_publish_with_absolute_at_resolves_scheduled_time(container: AsyncContainer) -> None:
    bus, endpoint = await _spy_bus(container, endpoint=_CapturingEndpoint(supports_scheduling=True))
    when = _NOW + timedelta(hours=2)

    await bus.schedule_publish(_Note(), at=when)

    assert endpoint.last is not None
    assert endpoint.last.scheduled_time == when


async def test_schedule_publish_with_relative_delay_resolves_scheduled_time(container: AsyncContainer) -> None:
    bus, endpoint = await _spy_bus(container, endpoint=_CapturingEndpoint(supports_scheduling=True))

    await bus.schedule_publish(_Note(), delay=timedelta(seconds=90))

    assert endpoint.last is not None
    assert endpoint.last.scheduled_time == _NOW + timedelta(seconds=90)


async def test_schedule_publish_with_zero_subscribers_is_silent_noop(container: AsyncContainer) -> None:
    bus, endpoint = await _spy_bus(container, routes=())

    await bus.schedule_publish(_Note(), delay=timedelta(seconds=5))

    assert endpoint.last is None


async def test_schedule_publish_mixed_subscribers_raises_scheduling_not_supported(container: AsyncContainer) -> None:
    capable = _CapturingEndpoint(uri='spy://durable', supports_scheduling=True)
    incapable = _CapturingEndpoint(uri='spy://buffered', supports_scheduling=False)
    bus = await _bus_with_endpoints(container, (capable, incapable))

    with pytest.raises(SchedulingNotSupportedError):
        await bus.schedule_publish(_Note(), at=_NOW + timedelta(hours=1))


async def test_send_drops_already_expired_message(container: AsyncContainer) -> None:
    bus, endpoint = await _spy_bus(container)
    past = _NOW - timedelta(seconds=1)

    await bus.send(_Note(), DeliveryOptions(deliver_by=past))

    assert endpoint.last is None


async def test_publish_drops_already_expired_message(container: AsyncContainer) -> None:
    bus, endpoint = await _spy_bus(container)
    past = _NOW - timedelta(seconds=1)

    await bus.publish(_Note(), DeliveryOptions(deliver_by=past))

    assert endpoint.last is None


async def test_send_does_not_drop_future_expiry(container: AsyncContainer) -> None:
    bus, endpoint = await _spy_bus(container)

    await bus.send(_Note(), DeliveryOptions(deliver_by=_NOW + timedelta(seconds=60)))

    assert endpoint.last is not None


async def test_send_drops_expired_message_before_route_resolution(container: AsyncContainer) -> None:
    # An expired message for an unrouted type drops silently — the expiry check precedes NoRouteError.
    bus, _ = await _spy_bus(container)
    past = _NOW - timedelta(seconds=1)

    await bus.send(_Cmd(name='unrouted'), DeliveryOptions(deliver_by=past))


async def test_send_scheduled_to_non_scheduling_endpoint_raises(container: AsyncContainer) -> None:
    bus, _ = await _spy_bus(container)  # default endpoint does not support scheduling

    with pytest.raises(SchedulingNotSupportedError):
        await bus.send(_Note(), DeliveryOptions(scheduled_time=_NOW + timedelta(hours=1)))


async def test_send_scheduled_to_scheduling_capable_endpoint_dispatches(container: AsyncContainer) -> None:
    capable = _CapturingEndpoint(supports_scheduling=True)
    bus, endpoint = await _spy_bus(container, endpoint=capable)

    await bus.send(_Note(), DeliveryOptions(scheduled_time=_NOW + timedelta(hours=1)))

    assert endpoint.last is not None


async def test_publish_scheduled_raises_when_any_subscriber_is_non_scheduling(container: AsyncContainer) -> None:
    capable = _CapturingEndpoint(uri='spy://durable', supports_scheduling=True)
    incapable = _CapturingEndpoint(uri='spy://buffered', supports_scheduling=False)
    bus = await _bus_with_endpoints(container, (capable, incapable))

    with pytest.raises(SchedulingNotSupportedError):
        await bus.publish(_Note(), DeliveryOptions(scheduled_time=_NOW + timedelta(hours=1)))
