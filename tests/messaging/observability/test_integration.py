import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import timedelta
from typing import Annotated, Any, cast

import anyio.lowlevel
import pytest
from typing_extensions import override

from waku.di import Scope, many, object_, scoped, singleton
from waku.messages import IEvent
from waku.messaging import (
    INVOKE_DESTINATION,
    Audit,
    CircuitBreakerConfig,
    EventHandler,
    HandlerType,
    IMessageBus,
    IMessageObserver,
    InboxConfig,
    IRequest,
    MessageEnvelope,
    MessagingConfig,
    MessagingExtension,
    MessagingModule,
    OutboxConfig,
    RequestHandler,
    TransactionalBehavior,
    external_endpoint,
)
from waku.messaging._internal.identifiers import EndpointUri
from waku.messaging.durability import IInboxStore, IOutboxStore
from waku.messaging.endpoints import ExecutionOutcome
from waku.messaging.endpoints.base import EndpointMode
from waku.messaging.inbox import InboxEntry, InboxStatus
from waku.messaging.inbox.destination import handler_destination
from waku.messaging.partition import ISequenceAllocator
from waku.messaging.router import local_queue, route
from waku.messaging.transport._internal.wire import encode_metadata, encode_payload
from waku.serialization.codec import PayloadCodec
from waku.testing import create_test_app
from waku.uow import IUnitOfWork

from tests._wait import wait_until
from tests.messaging.helpers import RecordingAllocator, RecordingTransport, RecordingUoW, make_envelope
from tests.messaging.inbox.fake_store import FakeInboxStore
from tests.messaging.outbox.fake_store import RecordingOutboxStore


class _MessageLogRecord(logging.LogRecord):
    audit: dict[str, object]
    destination: str
    outcome: str


def _message_record(record: logging.LogRecord) -> _MessageLogRecord:
    return cast('_MessageLogRecord', record)


@dataclass(frozen=True, slots=True)
class _Ping(IRequest[None]):
    ref: Annotated[str, Audit()] = ''


@dataclass(frozen=True, slots=True)
class _Ordered(IEvent):
    order_id: str


@dataclass(frozen=True, slots=True)
class _CbPing(IEvent):
    pass


@dataclass(frozen=True, slots=True)
class _DecoratedPing(IRequest[None]):
    pass


@dataclass(frozen=True, slots=True)
class _PlainPing(IRequest[None]):
    pass


class _EventSink:
    def __init__(self) -> None:
        self.events: list[str] = []


class _RecordingObserver(IMessageObserver):
    def __init__(self, sink: _EventSink) -> None:
        self._sink = sink

    @override
    async def on_executing(self, envelope: MessageEnvelope[Any], destination: str, handler_type: HandlerType) -> None:
        self._sink.events.append('executing')

    @override
    async def on_executed(
        self,
        envelope: MessageEnvelope[Any],
        destination: str,
        handler_type: HandlerType,
        outcome: ExecutionOutcome,
        exc: Exception | None,
        duration: timedelta,
    ) -> None:
        self._sink.events.append('executed')


class _GlobalRawObserver(IMessageObserver):
    def __init__(self, sink: _EventSink) -> None:
        self._sink = sink

    @override
    async def on_executing(self, envelope: MessageEnvelope[Any], destination: str, handler_type: HandlerType) -> None:
        self._sink.events.append('executing')

    @override
    async def on_executed(
        self,
        envelope: MessageEnvelope[Any],
        destination: str,
        handler_type: HandlerType,
        outcome: ExecutionOutcome,
        exc: Exception | None,
        duration: timedelta,
    ) -> None:
        self._sink.events.append('executed')


class _EndpointSink:
    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []


class _EndpointOnlyObserver(IMessageObserver):
    def __init__(self, sink: _EndpointSink) -> None:
        self._sink = sink

    @override
    async def on_sent(self, envelope: MessageEnvelope[Any], destination: str) -> None:
        self._sink.events.append(('sent', destination))

    @override
    async def on_executing(self, envelope: MessageEnvelope[Any], destination: str, handler_type: HandlerType) -> None:
        self._sink.events.append(('executing', destination))

    @override
    async def on_executed(
        self,
        envelope: MessageEnvelope[Any],
        destination: str,
        handler_type: HandlerType,
        outcome: ExecutionOutcome,
        exc: Exception | None,
        duration: timedelta,
    ) -> None:
        self._sink.events.append(('executed', destination))


@pytest.fixture
async def buffered_app() -> AsyncIterator[tuple[IMessageBus, list[_Ping]]]:
    calls: list[_Ping] = []

    class _PingHandler(RequestHandler[_Ping, None]):
        @override
        async def handle(self, request: _Ping, /) -> None:
            calls.append(request)

    config = MessagingConfig(
        endpoints=[local_queue('ping-q')],
        routing=[route(_Ping).to('ping-q')],
    )
    async with (
        create_test_app(
            imports=[MessagingModule.register(config)],
            extensions=[MessagingExtension().bind(_PingHandler)],
        ) as app,
        app.container() as container,
    ):
        bus = await container.get(IMessageBus)
        yield bus, calls


@pytest.fixture
async def invoke_app() -> AsyncIterator[tuple[IMessageBus, _Ping]]:
    class _InvokeHandler(RequestHandler[_Ping, None]):
        @override
        async def handle(self, request: _Ping, /) -> None: ...

    async with (
        create_test_app(
            imports=[MessagingModule.register()],
            extensions=[MessagingExtension().bind(_InvokeHandler)],
        ) as app,
        app.container() as container,
    ):
        bus = await container.get(IMessageBus)
        yield bus, _Ping(ref='invoke-only')


async def test_send_emits_sent_then_executed_with_audit(
    buffered_app: tuple[IMessageBus, list[_Ping]],
    caplog: pytest.LogCaptureFixture,
) -> None:
    bus, calls = buffered_app
    with caplog.at_level(logging.DEBUG, logger='waku.message'):
        await bus.send(_Ping(ref='r1'))
        await wait_until(lambda: len(calls) == 1)
        await wait_until(lambda: any(r.message == 'executed' for r in caplog.records))
    msgs = {r.message for r in caplog.records if r.name.startswith('waku.message.')}
    assert {'sent', 'executing', 'executed'} <= msgs
    executed = _message_record(next(r for r in caplog.records if r.message == 'executed'))
    assert executed.audit == {'ref': 'r1'}
    assert executed.destination == 'ping-q'


async def test_invoke_emits_executing_and_executed_without_sent(
    invoke_app: tuple[IMessageBus, _Ping],
    caplog: pytest.LogCaptureFixture,
) -> None:
    bus, query = invoke_app
    with caplog.at_level(logging.DEBUG, logger='waku.message'):
        await bus.invoke(query)
    records = [r for r in caplog.records if r.name.startswith('waku.message.')]
    msgs = {r.message for r in records}
    assert {'executing', 'executed'} <= msgs
    assert 'sent' not in msgs  # invoke has no hand-off: matches Wolverine (no Sent on inline)
    executed = _message_record(next(r for r in records if r.message == 'executed'))
    assert executed.destination == INVOKE_DESTINATION
    assert executed.outcome == 'SUCCESS'
    assert executed.audit == {'ref': 'invoke-only'}


async def test_publish_fan_out_fires_on_sent_per_destination(caplog: pytest.LogCaptureFixture) -> None:
    calls: list[_Ping] = []

    class _FanOutHandler(RequestHandler[_Ping, None]):
        @override
        async def handle(self, request: _Ping, /) -> None:
            calls.append(request)

    config = MessagingConfig(
        endpoints=[local_queue('ping-q1'), local_queue('ping-q2')],
        routing=[route(_Ping).to('ping-q1'), route(_Ping).to('ping-q2')],
    )
    async with (
        create_test_app(
            imports=[MessagingModule.register(config)],
            extensions=[MessagingExtension().bind(_FanOutHandler)],
        ) as app,
        app.container() as container,
    ):
        bus = await container.get(IMessageBus)
        with caplog.at_level(logging.DEBUG, logger='waku.message'):
            await bus.publish(_Ping(ref='fan'))
            await wait_until(lambda: len(calls) == 2)

    sent = sorted(
        _message_record(r).destination
        for r in caplog.records
        if r.name.startswith('waku.message.') and r.message == 'sent'
    )
    assert sent == ['ping-q1', 'ping-q2']


async def test_inline_endpoint_fires_executing_and_executed_but_not_sent(caplog: pytest.LogCaptureFixture) -> None:
    calls: list[_Ping] = []

    class _InlineHandler(RequestHandler[_Ping, None]):
        @override
        async def handle(self, request: _Ping, /) -> None:
            calls.append(request)

    config = MessagingConfig(
        endpoints=[local_queue('ping-inline', mode=EndpointMode.INLINE)],
        routing=[route(_Ping).to('ping-inline')],
    )
    async with (
        create_test_app(
            imports=[MessagingModule.register(config)],
            extensions=[MessagingExtension().bind(_InlineHandler)],
        ) as app,
        app.container() as container,
    ):
        bus = await container.get(IMessageBus)
        with caplog.at_level(logging.DEBUG, logger='waku.message'):
            await bus.send(_Ping(ref='inline-1'))

    assert calls == [_Ping(ref='inline-1')]
    msgs = {r.message for r in caplog.records if r.name.startswith('waku.message.')}
    assert {'executing', 'executed'} <= msgs
    assert 'sent' not in msgs  # distinct from invoke(): INLINE still dispatches through an endpoint, just never sends


async def test_durable_and_drainer_paths_fire_executing_and_executed(caplog: pytest.LogCaptureFixture) -> None:
    inbox = FakeInboxStore()
    calls: list[str] = []

    class _DurableHandler(EventHandler[_Ordered]):
        @override
        async def handle(self, event: _Ordered, /) -> None:
            calls.append(event.order_id)

    config = MessagingConfig(
        endpoints=[local_queue('orders', mode=EndpointMode.DURABLE, stop_timeout=timedelta(seconds=1.0))],
        routing=[route(_Ordered).to('orders')],
        inbox=InboxConfig(owner_id='node-a:1', recovery_interval=timedelta(seconds=0.01)),
        global_pipeline_behaviors=[TransactionalBehavior],
    )
    async with (
        create_test_app(
            imports=[MessagingModule.register(config)],
            extensions=[MessagingExtension().bind(_DurableHandler)],
            providers=[
                object_(RecordingUoW(), provided_type=IUnitOfWork),
                object_(inbox, provided_type=IInboxStore),
                object_(RecordingAllocator(), provided_type=ISequenceAllocator),
            ],
        ) as app,
        app.container() as container,
    ):
        bus = await container.get(IMessageBus)
        with caplog.at_level(logging.DEBUG, logger='waku.message'):
            # Live path: the durable endpoint persists then enqueues, the worker drives the executor.
            await bus.publish(_Ordered(order_id='live-1'))
            await wait_until(lambda: calls == ['live-1'])

            # Recovery path: seed an unclaimed INCOMING row; InboxRecoveryWorker -> InboxDrainer executes it.
            codec = await app.container.get(PayloadCodec)
            envelope = make_envelope(_Ordered(order_id='recovered-1'))
            entry = InboxEntry(
                id=envelope.message_id,
                payload=encode_payload(envelope, codec),
                message_type=envelope.message_type,
                source_uri=EndpointUri('orders'),
                destination=handler_destination(_DurableHandler),
                owner_id=None,
                status=InboxStatus.INCOMING,
                correlation_id=envelope.correlation_id,
                causation_id=envelope.causation_id,
                metadata=encode_metadata(envelope),
            )
            inbox.entries[entry.id, entry.destination] = entry
            await wait_until(lambda: sorted(calls) == ['live-1', 'recovered-1'])

    msgs = {r.message for r in caplog.records if r.name.startswith('waku.message.')}
    assert {'executing', 'executed'} <= msgs


async def test_circuit_breaker_and_observers_executed_coexist(caplog: pytest.LogCaptureFixture) -> None:
    class _AlwaysFailHandler(EventHandler[_CbPing]):
        @override
        async def handle(self, event: _CbPing, /) -> None:
            msg = 'boom'
            raise RuntimeError(msg)

    config = MessagingConfig(
        endpoints=[
            local_queue(
                'cb-q',
                circuit_breaker=CircuitBreakerConfig(
                    minimum_throughput=2,
                    failure_rate_threshold=0.5,
                    pause_time=timedelta(minutes=5),  # large: the timed resume must NOT fire during the test
                ),
            )
        ],
        routing=[route(_CbPing).to('cb-q')],
    )
    async with (
        create_test_app(
            imports=[MessagingModule.register(config)],
            extensions=[MessagingExtension().bind(_AlwaysFailHandler)],
        ) as app,
        app.container() as container,
    ):
        bus = await container.get(IMessageBus)

        def executed_count() -> int:
            return len([r for r in caplog.records if r.name.startswith('waku.message.') and r.message == 'executed'])

        with caplog.at_level(logging.DEBUG, logger='waku.message'):
            # Dispatch the first two sequentially, settling each: on_result (CB.record) fires AFTER
            # observers.executed, so "executed_count() == N" alone doesn't prove the Nth record() ran.
            await bus.publish(_CbPing())
            await wait_until(lambda: executed_count() == 1)
            await bus.publish(_CbPing())
            await wait_until(lambda: executed_count() == 2)
            for _ in range(50):  # let the 2nd record()'s trip() (and its pause()) fully settle
                await anyio.lowlevel.checkpoint()

            # The breaker is now OPEN -> further messages are gated (an externally observable CB effect).
            await bus.publish(_CbPing())
            await bus.publish(_CbPing())
            for _ in range(50):
                await anyio.lowlevel.checkpoint()

            # Assert BEFORE the app (and its endpoints) shut down: endpoint.stop() force-resumes the
            # pause gate and drains whatever is still buffered, which would process the gated messages too.
            assert executed_count() == 2  # CB's on_result gated the remaining 2 AND observers.executed fired for both


async def test_config_declared_observer_is_di_constructed_and_fires() -> None:
    class _RecordedPingHandler(RequestHandler[_Ping, None]):
        @override
        async def handle(self, request: _Ping, /) -> None: ...

    config = MessagingConfig(
        endpoints=[local_queue('rec-q')],
        routing=[route(_Ping).to('rec-q')],
        observers=(_RecordingObserver,),
    )
    async with (
        create_test_app(
            imports=[MessagingModule.register(config)],
            extensions=[MessagingExtension().bind(_RecordedPingHandler)],
            providers=[singleton(_EventSink)],
        ) as app,
        app.container() as container,
    ):
        bus = await container.get(IMessageBus)
        sink = await container.get(_EventSink)
        await bus.send(_Ping(ref='r1'))
        await wait_until(lambda: sink.events == ['executing', 'executed'])

    assert sink.events == ['executing', 'executed']  # constructor-injected sink observed both hooks


async def test_default_logging_observer_retained_when_config_observers_declared(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class _RecordedPingHandler(RequestHandler[_Ping, None]):
        @override
        async def handle(self, request: _Ping, /) -> None: ...

    config = MessagingConfig(
        endpoints=[local_queue('rec-q')],
        routing=[route(_Ping).to('rec-q')],
        observers=(_RecordingObserver,),
    )
    async with (
        create_test_app(
            imports=[MessagingModule.register(config)],
            extensions=[MessagingExtension().bind(_RecordedPingHandler)],
            providers=[singleton(_EventSink)],
        ) as app,
        app.container() as container,
    ):
        bus = await container.get(IMessageBus)
        sink = await container.get(_EventSink)
        with caplog.at_level(logging.DEBUG, logger='waku.message'):
            await bus.send(_Ping(ref='r1'))
            await wait_until(lambda: sink.events == ['executing', 'executed'])
            await wait_until(lambda: any(r.message == 'executed' for r in caplog.records))

    assert sink.events == ['executing', 'executed']  # the declared observer fired
    msgs = {r.message for r in caplog.records if r.name.startswith('waku.message.')}
    assert {'executing', 'executed'} <= msgs  # ...and so did the built-in logging observer (extend, not replace)


async def test_duplicate_observer_declaration_registers_once() -> None:
    class _RecordedPingHandler(RequestHandler[_Ping, None]):
        @override
        async def handle(self, request: _Ping, /) -> None: ...

    config = MessagingConfig(
        endpoints=[local_queue('rec-q')],
        routing=[route(_Ping).to('rec-q')],
        observers=(_RecordingObserver, _RecordingObserver),
    )
    async with (
        create_test_app(
            imports=[MessagingModule.register(config)],
            extensions=[MessagingExtension().bind(_RecordedPingHandler)],
            providers=[singleton(_EventSink)],
        ) as app,
        app.container() as container,
    ):
        bus = await container.get(IMessageBus)
        sink = await container.get(_EventSink)
        await bus.send(_Ping(ref='r1'))
        await wait_until(lambda: sink.events.count('executing') == 1)

    assert sink.events.count('executing') == 1  # deduped: registered once despite the duplicate declaration


async def test_endpoint_declared_observer_fires_only_for_its_uri_global_fires_for_both() -> None:
    class _DecoratedHandler(RequestHandler[_DecoratedPing, None]):
        @override
        async def handle(self, request: _DecoratedPing, /) -> None: ...

    class _PlainHandler(RequestHandler[_PlainPing, None]):
        @override
        async def handle(self, request: _PlainPing, /) -> None: ...

    config = MessagingConfig(
        endpoints=[
            local_queue('decorated-q', observers=(_EndpointOnlyObserver,)),
            local_queue('plain-q'),
        ],
        routing=[route(_DecoratedPing).to('decorated-q'), route(_PlainPing).to('plain-q')],
        observers=(_RecordingObserver,),
    )
    async with (
        create_test_app(
            imports=[MessagingModule.register(config)],
            extensions=[MessagingExtension().bind(_DecoratedHandler, _PlainHandler)],
            providers=[singleton(_EventSink), singleton(_EndpointSink)],
        ) as app,
        app.container() as container,
    ):
        bus = await container.get(IMessageBus)
        endpoint_sink = await container.get(_EndpointSink)
        global_sink = await container.get(_EventSink)

        await bus.send(_DecoratedPing())
        await wait_until(lambda: len(endpoint_sink.events) == 3)

        await bus.send(_PlainPing())
        await wait_until(lambda: global_sink.events.count('executed') == 2)

    # endpoint-declared observer: only decorated-q traffic, full sent/executing/executed sequence
    assert [event for event, _dest in endpoint_sink.events] == ['sent', 'executing', 'executed']
    assert {dest for _event, dest in endpoint_sink.events} == {'decorated-q'}
    # global observer: fired for both destinations
    assert global_sink.events.count('executing') == 2
    assert global_sink.events.count('executed') == 2


async def test_external_endpoint_declared_observer_fires_on_sent() -> None:
    class _OrderedHandler(EventHandler[_Ordered]):
        @override
        async def handle(self, event: _Ordered, /) -> None: ...

    config = MessagingConfig(
        endpoints=[external_endpoint('test://events', observers=(_EndpointOnlyObserver,))],
        routing=[route(_Ordered).to('test://events')],
        outbox=OutboxConfig(),
        transports={'test': RecordingTransport},
        global_pipeline_behaviors=[TransactionalBehavior],
    )
    async with (
        create_test_app(
            imports=[MessagingModule.register(config)],
            extensions=[MessagingExtension().bind(_OrderedHandler)],
            providers=[
                object_(RecordingUoW(), provided_type=IUnitOfWork),
                scoped(IOutboxStore, RecordingOutboxStore),
                singleton(_EndpointSink),
            ],
        ) as app,
        app.container() as container,
    ):
        bus = await container.get(IMessageBus)
        endpoint_sink = await container.get(_EndpointSink)
        await bus.publish(_Ordered(order_id='ext-1'))

    assert endpoint_sink.events == [('sent', 'test://events')]  # sender-side attach on the outbox endpoint


async def test_raw_many_provider_observer_fires_globally_without_config_declaration() -> None:
    class _RecordedPingHandler(RequestHandler[_Ping, None]):
        @override
        async def handle(self, request: _Ping, /) -> None: ...

    config = MessagingConfig(
        endpoints=[local_queue('rec-q')],
        routing=[route(_Ping).to('rec-q')],
    )
    async with (
        create_test_app(
            imports=[MessagingModule.register(config)],
            extensions=[MessagingExtension().bind(_RecordedPingHandler)],
            providers=[
                singleton(_EventSink),
                many(IMessageObserver, _GlobalRawObserver, scope=Scope.APP, collect=False),
            ],
        ) as app,
        app.container() as container,
    ):
        bus = await container.get(IMessageBus)
        sink = await container.get(_EventSink)
        await bus.send(_Ping(ref='r1'))
        await wait_until(lambda: sink.events == ['executing', 'executed'])

    assert sink.events == ['executing', 'executed']  # raw many() registration still fires as a global observer


async def test_invoke_does_not_fire_endpoint_declared_observer_but_fires_global() -> None:
    class _InvokeHandler(RequestHandler[_Ping, None]):
        @override
        async def handle(self, request: _Ping, /) -> None: ...

    config = MessagingConfig(
        endpoints=[local_queue('side-q', observers=(_EndpointOnlyObserver,))],
        observers=(_RecordingObserver,),
    )
    async with (
        create_test_app(
            imports=[MessagingModule.register(config)],
            extensions=[MessagingExtension().bind(_InvokeHandler)],
            providers=[singleton(_EventSink), singleton(_EndpointSink)],
        ) as app,
        app.container() as container,
    ):
        bus = await container.get(IMessageBus)
        endpoint_sink = await container.get(_EndpointSink)
        global_sink = await container.get(_EventSink)
        await bus.invoke(_Ping(ref='invoke-1'))

    assert endpoint_sink.events == []  # endpoint-declared observer never fires on the endpoint-less invoke path
    assert global_sink.events == ['executing', 'executed']  # global tier is invoke-visible
