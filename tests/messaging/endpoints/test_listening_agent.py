from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any

import anyio
import pytest
from dishka import AsyncContainer, Provider, Scope, make_async_container, provide
from typing_extensions import override

from waku.di import object_, scoped
from waku.exceptions import ImproperlyConfiguredError
from waku.messages import IEvent
from waku.messaging import (
    HandlerMap,
    InboxConfig,
    ISequenceAllocator,
    MessagingConfig,
    MessagingExtension,
    MessagingModule,
    TransactionalBehavior,
)
from waku.messaging._internal.identity import MessageTypeRegistry
from waku.messaging.circuit_breaker.config import CircuitBreakerConfig
from waku.messaging.durability import IInboxStore
from waku.messaging.endpoints._internal.durable_inbox_receiver import DurableInboxReceiver
from waku.messaging.endpoints._internal.listening_agent import (
    ListeningAgent,
    ListeningStatus,
    create_listening_agent,
)
from waku.messaging.endpoints._internal.merge import merge_broker_endpoints
from waku.messaging.endpoints.executor import EndpointExecutor, EndpointExecutorFactory, ExecutionResult
from waku.messaging.endpoints.outcome import ExecutionOutcome
from waku.messaging.handler import EventHandler
from waku.messaging.inbox._internal.listener import InboundListener
from waku.messaging.inbox.backpressure import BufferingLimits
from waku.messaging.inbox.models import InboxStatus
from waku.messaging.router import external_endpoint, listen
from waku.messaging.transport._internal.registry import TransportRegistry
from waku.messaging.transport._internal.wire import encode_payload, envelope_metadata_of
from waku.messaging.transport.inbound import ConsumeDisposition
from waku.messaging.transport.interfaces import IEnvelopeMapper, ITransport, Subscription
from waku.serialization.codec import PayloadCodec
from waku.testing import create_test_app
from waku.uow import IUnitOfWork

from tests._wait import wait_until
from tests.messaging.helpers import RecordingAllocator, RecordingUoW, make_codec, make_envelope
from tests.messaging.inbox.fake_store import FakeInboxStore

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from waku.application import WakuApplication
    from waku.messaging.endpoints._internal.merge import MergedBrokerEndpoint
    from waku.messaging.transport.inbound import ConsumeCallback
    from waku.messaging.transport.interfaces import EnvelopeMetadata

_URI = 'test://orders'


@dataclass(frozen=True, slots=True)
class _OrderPlaced(IEvent):
    order_id: str


class _OrderHandler(EventHandler[_OrderPlaced]):
    @override
    async def handle(self, event: _OrderPlaced, /) -> None: ...


class _RecordingSubscription(Subscription):
    def __init__(self) -> None:
        self.events: list[str] = []

    @override
    async def pause(self) -> None:
        self.events.append('pause')

    @override
    async def resume(self) -> None:
        self.events.append('resume')


class _SpyTransport(ITransport):
    def __init__(self) -> None:
        self.subscription = _RecordingSubscription()
        self.subscribed: list[tuple[str, ConsumeCallback, IEnvelopeMapper[Any, Any] | None]] = []

    @override
    async def send(
        self,
        body: dict[str, Any],
        *,
        destination: str,
        metadata: EnvelopeMetadata,
        mapper: IEnvelopeMapper[Any, Any] | None = None,
    ) -> None: ...

    @override
    def subscribe(
        self,
        queue: str,
        on_message: ConsumeCallback,
        mapper: IEnvelopeMapper[Any, Any] | None = None,
    ) -> Subscription:
        self.subscribed.append((queue, on_message, mapper))
        return self.subscription

    @override
    async def start(self) -> None: ...

    @override
    async def stop(self) -> None: ...


class _DepsProvider(Provider):
    scope = Scope.REQUEST

    def __init__(self, inbox: IInboxStore) -> None:
        super().__init__()
        self._inbox = inbox
        self._codec = make_codec()
        self._uow: IUnitOfWork = RecordingUoW()

    @provide
    def inbox(self) -> IInboxStore:
        return self._inbox

    @provide(scope=Scope.APP)
    def codec(self) -> PayloadCodec:
        return self._codec

    @provide
    def uow(self) -> IUnitOfWork:
        return self._uow


class _FailingExecutor(EndpointExecutor):
    def __init__(self) -> None:
        # Bypass parent __init__: these tests never exercise real dispatch.
        self.failure = RuntimeError('handler failure')

    @override
    async def execute(
        self,
        envelope: object,
        handler_type: object,
        *,
        on_result: Callable[[ExecutionOutcome, Exception | None], Awaitable[None]] | None = None,
    ) -> ExecutionResult:
        assert on_result is not None  # the receiver always feeds its circuit breaker
        await on_result(ExecutionOutcome.FAILED_NO_POLICY, self.failure)
        return ExecutionResult(outcome=ExecutionOutcome.FAILED_NO_POLICY)


def _make_receiver(
    container: AsyncContainer,
    *,
    receiver_cls: type[DurableInboxReceiver] = DurableInboxReceiver,
) -> DurableInboxReceiver:
    return receiver_cls(
        uri=_URI,
        container=container,
        executor=_FailingExecutor(),
        inbox_owner_id='node-a:1',
        keep_after_handled=timedelta(seconds=300),
        max_buffer_size=100,
        stop_timeout=timedelta(seconds=1.0),
    )


def _make_merged(*, cb_config: CircuitBreakerConfig | None, limits: BufferingLimits | None) -> MergedBrokerEndpoint:
    entry = listen(_URI, circuit_breaker=cb_config, backpressure=limits)
    return merge_broker_endpoints([entry], inbox_configured=True)[0]


def _make_agent(
    container: AsyncContainer,
    transport: _SpyTransport,
    *,
    cb_config: CircuitBreakerConfig | None = None,
    limits: BufferingLimits | None = None,
    sleep: Callable[[float], Awaitable[None]] = anyio.sleep,
    receiver: DurableInboxReceiver | None = None,
) -> tuple[ListeningAgent, DurableInboxReceiver]:
    receiver = receiver if receiver is not None else _make_receiver(container)
    listener = InboundListener(
        codec=make_codec(),
        type_registry=MessageTypeRegistry(identities={}, known_types=[_OrderPlaced]),
        handler_map=HandlerMap(),
        receiver=receiver,
    )
    agent = ListeningAgent(
        merged=_make_merged(cb_config=cb_config, limits=limits),
        registry=TransportRegistry({'test': transport}),
        receiver=receiver,
        listener=listener,
        cb_config=cb_config,
        limits=limits,
        sleep=sleep,
    )
    return agent, receiver


def _assert_status(agent: ListeningAgent, expected: ListeningStatus) -> None:
    assert agent.status is expected


def _assert_flags(agent: ListeningAgent, *, cb_paused: bool, watermark_held: bool) -> None:
    assert agent.cb_paused is cb_paused
    assert agent.watermark_held is watermark_held


class _UnstartableReceiver(DurableInboxReceiver):
    @override
    async def start(self, *, on_drain: Callable[[int], Awaitable[None]] | None = None) -> None:
        msg = 'broker connection refused'
        raise RuntimeError(msg)


class _GatedSleep:
    def __init__(self) -> None:
        self.release = anyio.Event()
        self.requested: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.requested.append(seconds)
        await self.release.wait()


async def _trip_breaker(receiver: DurableInboxReceiver) -> None:
    # Feed the wired breaker directly (plan-sanctioned) — deterministic: the trip's gate pause is awaited inline.
    await receiver._circuit_breaker.record(ExecutionOutcome.FAILED_NO_POLICY, RuntimeError('boom'))  # noqa: SLF001


class TestListeningAgentConstruction:
    @staticmethod
    async def test_construction_is_passive_and_reports_stopped() -> None:
        async with make_async_container(_DepsProvider(FakeInboxStore())) as container:
            transport = _SpyTransport()
            agent, _ = _make_agent(container, transport, limits=BufferingLimits(high=2, low=0))

            _assert_status(agent, ListeningStatus.STOPPED)
            _assert_flags(agent, cb_paused=False, watermark_held=False)
            assert agent.uri == _URI
            assert agent.queue_depth == 0
            assert transport.subscribed == []
            assert transport.subscription.events == []


class TestListeningAgentBackpressure:
    @staticmethod
    async def test_high_watermark_sets_too_busy_and_pauses_listener_until_drain() -> None:
        async with make_async_container(_DepsProvider(FakeInboxStore())) as container:
            transport = _SpyTransport()
            agent, _ = _make_agent(container, transport, limits=BufferingLimits(high=2, low=0))
            await agent.start()
            _assert_status(agent, ListeningStatus.ACCEPTING)

            await agent._observe_depth(2)  # noqa: SLF001 -- the seam both wired depth paths route through
            _assert_status(agent, ListeningStatus.TOO_BUSY)
            _assert_flags(agent, cb_paused=False, watermark_held=True)
            assert transport.subscription.events == ['pause']

            await agent._observe_depth(1)  # noqa: SLF001
            _assert_status(agent, ListeningStatus.TOO_BUSY)  # hysteresis: held until the low watermark

            await agent._observe_depth(0)  # noqa: SLF001
            _assert_status(agent, ListeningStatus.ACCEPTING)
            _assert_flags(agent, cb_paused=False, watermark_held=False)
            assert transport.subscription.events == ['pause', 'resume']

            await agent.stop()
            _assert_status(agent, ListeningStatus.STOPPED)

    @staticmethod
    async def test_agent_without_watermark_or_breaker_never_pauses() -> None:
        async with make_async_container(_DepsProvider(FakeInboxStore())) as container:
            transport = _SpyTransport()
            agent, _ = _make_agent(container, transport)
            await agent.start()
            _assert_status(agent, ListeningStatus.ACCEPTING)

            await agent._observe_depth(10_000)  # noqa: SLF001
            _assert_status(agent, ListeningStatus.ACCEPTING)
            _assert_flags(agent, cb_paused=False, watermark_held=False)
            assert transport.subscription.events == []

            await agent.stop()


class TestListeningAgentCircuitBreaker:
    @staticmethod
    async def test_breaker_trip_pauses_listener_then_timed_resume_reopens() -> None:
        inbox = FakeInboxStore()
        sleep = _GatedSleep()
        async with make_async_container(_DepsProvider(inbox)) as container:
            transport = _SpyTransport()
            agent, receiver = _make_agent(
                container,
                transport,
                cb_config=CircuitBreakerConfig(minimum_throughput=1, pause_time=timedelta(minutes=5)),
                sleep=sleep,
            )
            await agent.start()

            envelope = make_envelope(_OrderPlaced(order_id='o-1'))
            fresh = await receiver.persist(envelope, frozenset([_OrderHandler]))
            await receiver.enqueue(envelope, fresh)

            await wait_until(lambda: transport.subscription.events == ['pause'])
            _assert_status(agent, ListeningStatus.PAUSED)
            _assert_flags(agent, cb_paused=True, watermark_held=False)
            # pause_time reaches the injected sleep once the resume task takes its first step — no wall clock.
            await wait_until(lambda: sleep.requested == [300.0])

            sleep.release.set()
            await wait_until(lambda: transport.subscription.events == ['pause', 'resume'])
            _assert_status(agent, ListeningStatus.ACCEPTING)
            _assert_flags(agent, cb_paused=False, watermark_held=False)

            await agent.stop()

    @staticmethod
    async def test_cb_hold_outranks_watermark_and_survives_watermark_release() -> None:
        sleep = _GatedSleep()
        async with make_async_container(_DepsProvider(FakeInboxStore())) as container:
            transport = _SpyTransport()
            agent, receiver = _make_agent(
                container,
                transport,
                cb_config=CircuitBreakerConfig(minimum_throughput=1, pause_time=timedelta(minutes=5)),
                limits=BufferingLimits(high=2, low=0),
                sleep=sleep,
            )
            await agent.start()

            await _trip_breaker(receiver)
            _assert_status(agent, ListeningStatus.PAUSED)
            assert transport.subscription.events == ['pause']

            await agent._observe_depth(2)  # noqa: SLF001 -- concurrent hold: watermark joins the CB's pause
            _assert_flags(agent, cb_paused=True, watermark_held=True)
            _assert_status(agent, ListeningStatus.PAUSED)  # PAUSED > TOO_BUSY
            assert transport.subscription.events == ['pause']  # one refcounted gate: no second broker pause

            await agent._observe_depth(0)  # noqa: SLF001 -- watermark releases while the CB still holds
            _assert_flags(agent, cb_paused=True, watermark_held=False)
            _assert_status(agent, ListeningStatus.PAUSED)
            assert transport.subscription.events == ['pause']

            sleep.release.set()
            await wait_until(lambda: transport.subscription.events == ['pause', 'resume'])
            _assert_status(agent, ListeningStatus.ACCEPTING)

            await agent.stop()


class TestListeningAgentLifecycle:
    @staticmethod
    async def test_double_start_subscribes_once() -> None:
        async with make_async_container(_DepsProvider(FakeInboxStore())) as container:
            transport = _SpyTransport()
            agent, _ = _make_agent(container, transport, limits=BufferingLimits(high=2, low=0))
            await agent.start()
            await agent.start()

            assert len(transport.subscribed) == 1
            _assert_status(agent, ListeningStatus.ACCEPTING)

            await agent.stop()

    @staticmethod
    async def test_stop_before_start_and_double_stop_are_noops() -> None:
        async with make_async_container(_DepsProvider(FakeInboxStore())) as container:
            transport = _SpyTransport()
            agent, _ = _make_agent(container, transport)

            await agent.stop()
            _assert_status(agent, ListeningStatus.STOPPED)

            await agent.start()
            await agent.stop()
            await agent.stop()
            _assert_status(agent, ListeningStatus.STOPPED)

    @staticmethod
    async def test_stop_from_too_busy_returns_stopped() -> None:
        async with make_async_container(_DepsProvider(FakeInboxStore())) as container:
            transport = _SpyTransport()
            agent, _ = _make_agent(container, transport, limits=BufferingLimits(high=2, low=0))
            await agent.start()
            await agent._observe_depth(2)  # noqa: SLF001
            _assert_status(agent, ListeningStatus.TOO_BUSY)

            await agent.stop()
            _assert_status(agent, ListeningStatus.STOPPED)

    @staticmethod
    async def test_stop_from_paused_returns_stopped() -> None:
        sleep = _GatedSleep()
        async with make_async_container(_DepsProvider(FakeInboxStore())) as container:
            transport = _SpyTransport()
            agent, receiver = _make_agent(
                container,
                transport,
                cb_config=CircuitBreakerConfig(minimum_throughput=1, pause_time=timedelta(minutes=5)),
                sleep=sleep,
            )
            await agent.start()
            await _trip_breaker(receiver)
            _assert_status(agent, ListeningStatus.PAUSED)

            await agent.stop()
            _assert_status(agent, ListeningStatus.STOPPED)

    @staticmethod
    async def test_stop_after_failed_start_returns_stopped() -> None:
        async with make_async_container(_DepsProvider(FakeInboxStore())) as container:
            transport = _SpyTransport()
            receiver = _make_receiver(container, receiver_cls=_UnstartableReceiver)
            agent, _ = _make_agent(container, transport, receiver=receiver)

            with pytest.raises(RuntimeError, match='broker connection refused'):
                await agent.start()
            _assert_status(agent, ListeningStatus.STARTING)

            await agent.stop()
            _assert_status(agent, ListeningStatus.STOPPED)


async def _factory_agent(
    app: WakuApplication,
    config: MessagingConfig,
    transport: _SpyTransport,
    merged: MergedBrokerEndpoint,
) -> ListeningAgent:
    assert config.inbox is not None
    return create_listening_agent(
        merged,
        container=app.container,
        executor_factory=await app.container.get(EndpointExecutorFactory),
        registry=TransportRegistry({'test': transport}),
        codec=await app.container.get(PayloadCodec),
        type_registry=await app.container.get(MessageTypeRegistry),
        handler_map=await app.container.get(HandlerMap),
        inbox=config.inbox,
        config=config,
    )


class TestCreateListeningAgent:
    @staticmethod
    async def test_factory_built_graph_processes_inbound_message_end_to_end() -> None:
        handled: list[str] = []

        class _FlowHandler(EventHandler[_OrderPlaced]):
            @override
            async def handle(self, event: _OrderPlaced, /) -> None:
                handled.append(event.order_id)

        inbox = FakeInboxStore()
        transport = _SpyTransport()
        config = MessagingConfig(
            inbox=InboxConfig(owner_id='test-node:1'),
            global_pipeline_behaviors=[TransactionalBehavior],
        )
        async with create_test_app(
            imports=[MessagingModule.register(config)],
            extensions=[MessagingExtension().bind(_FlowHandler)],
            providers=[
                object_(RecordingUoW(), provided_type=IUnitOfWork),
                object_(inbox, provided_type=IInboxStore),
                object_(RecordingAllocator(), provided_type=ISequenceAllocator),
            ],
        ) as app:
            merged = merge_broker_endpoints([listen(_URI, max_requeue_attempts=3)], inbox_configured=True)[0]
            agent = await _factory_agent(app, config, transport, merged)
            await agent.start()
            _assert_status(agent, ListeningStatus.ACCEPTING)

            queue, on_message, _mapper = transport.subscribed[0]
            assert queue == 'orders'
            codec = await app.container.get(PayloadCodec)
            envelope = make_envelope(_OrderPlaced(order_id='flow-1'))
            disposition = await on_message(encode_payload(envelope, codec), envelope_metadata_of(envelope))
            assert disposition is ConsumeDisposition.ACK
            await wait_until(lambda: handled == ['flow-1'])
            await agent.stop()

        entries = list(inbox.entries.values())
        assert len(entries) == 1
        assert entries[0].status is InboxStatus.HANDLED

    @staticmethod
    async def test_factory_rejects_endpoint_without_listen_aspect() -> None:
        transport = _SpyTransport()
        config = MessagingConfig(
            inbox=InboxConfig(owner_id='test-node:1'),
            global_pipeline_behaviors=[TransactionalBehavior],
        )
        async with create_test_app(
            imports=[MessagingModule.register(config)],
            providers=[
                object_(RecordingUoW(), provided_type=IUnitOfWork),
                object_(RecordingAllocator(), provided_type=ISequenceAllocator),
                scoped(IInboxStore, FakeInboxStore),
            ],
        ) as app:
            merged = merge_broker_endpoints([external_endpoint(_URI)], inbox_configured=True)[0]
            with pytest.raises(ImproperlyConfiguredError, match='declares no listen aspect'):
                await _factory_agent(app, config, transport, merged)

    @staticmethod
    async def test_extension_built_graph_delivers_the_same_inbound_flow() -> None:
        handled: list[str] = []

        class _FlowHandler(EventHandler[_OrderPlaced]):
            @override
            async def handle(self, event: _OrderPlaced, /) -> None:
                handled.append(event.order_id)

        inbox = FakeInboxStore()
        transport = _SpyTransport()
        config = MessagingConfig(
            endpoints=[listen(_URI)],
            inbox=InboxConfig(owner_id='test-node:1'),
            transports={'test': lambda: transport},
            global_pipeline_behaviors=[TransactionalBehavior],
        )
        async with create_test_app(
            imports=[MessagingModule.register(config)],
            extensions=[MessagingExtension().bind(_FlowHandler)],
            providers=[
                object_(RecordingUoW(), provided_type=IUnitOfWork),
                object_(inbox, provided_type=IInboxStore),
                object_(RecordingAllocator(), provided_type=ISequenceAllocator),
            ],
        ) as app:
            codec = await app.container.get(PayloadCodec)
            envelope = make_envelope(_OrderPlaced(order_id='ext-1'))
            _queue, on_message, _mapper = transport.subscribed[0]
            disposition = await on_message(encode_payload(envelope, codec), envelope_metadata_of(envelope))
            assert disposition is ConsumeDisposition.ACK
            await wait_until(lambda: handled == ['ext-1'])

        entries = list(inbox.entries.values())
        assert len(entries) == 1
        assert entries[0].status is InboxStatus.HANDLED
