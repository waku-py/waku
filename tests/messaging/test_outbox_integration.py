from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar, cast

import anyio
import pytest
from typing_extensions import override

from waku.backends.memory import MemoryBackend
from waku.di import object_, scoped
from waku.messages import IEvent
from waku.messaging import (
    EventHandler,
    IMessageBus,
    IRequest,
    MessagingConfig,
    MessagingExtension,
    MessagingModule,
    OutboxConfig,
    RequestHandler,
    TransactionalBehavior,
    external_endpoint,
    local_queue,
    route,
)
from waku.messaging.durability import IDurabilityStore, IOutboxStore
from waku.messaging.observability.observer import IMessageObserver
from waku.testing import create_test_app
from waku.uow import IUnitOfWork

from tests._wait import wait_until
from tests.messaging.helpers import (
    RecordingDeadLetterStore,
    RecordingDurabilityStore,
    RecordingTransport,
    RecordingUoW,
)
from tests.messaging.inbox.fake_store import FakeInboxStore
from tests.messaging.outbox.fake_store import RecordingOutboxStore

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Sequence

    from dishka import Provider

    from waku.backends.memory._internal.outbox import WorkspaceOutboxStore
    from waku.messaging.contracts.envelope import MessageEnvelope
    from waku.messaging.outbox.models import OutboxMessage


@dataclass(frozen=True, slots=True)
class _OrderPlaced(IEvent):
    order_id: str


@dataclass(frozen=True, slots=True)
class _Unrouted(IEvent):
    value: str = 'x'


@dataclass(frozen=True, slots=True)
class _Ping(IRequest[None]):
    name: str


class _ExternalRoutedHandler(EventHandler[_OrderPlaced]):
    # Bound only to satisfy route() validation; _OrderPlaced routes to an external (outbox) endpoint,
    # so this local handler is never invoked.
    @override
    async def handle(self, event: _OrderPlaced, /) -> None:
        pass  # pragma: no cover


class _LocalOrderHandler(EventHandler[_OrderPlaced]):
    received: ClassVar[list[str]] = []

    @override
    async def handle(self, event: _OrderPlaced, /) -> None:
        self.received.append(event.order_id)


class _PingHandler(RequestHandler[_Ping, None]):
    @override
    async def handle(self, request: _Ping, /) -> None:
        pass


class _SentSink:
    """Ordered record of ``sent`` evidence — fired only by the owner, only after the durable commit."""

    def __init__(self) -> None:
        self.destinations: list[str] = []


class _SentObserver(IMessageObserver):
    def __init__(self, sink: _SentSink) -> None:
        self._sink = sink

    @override
    async def on_sent(self, envelope: MessageEnvelope[Any], destination: str) -> None:
        self._sink.destinations.append(destination)


class _OutboxUnavailableError(Exception):
    pass


class _FailingOutboxStore(RecordingOutboxStore):
    @override
    async def save_batch(self, messages: Sequence[OutboxMessage]) -> None:
        raise _OutboxUnavailableError


class _FailOnSecondOutboxStore(RecordingOutboxStore):
    """Persists the first staged batch, then raises — proves a partial fan-out rolls the owner tx back."""

    @override
    async def save_batch(self, messages: Sequence[OutboxMessage]) -> None:
        if self.saved:
            raise _OutboxUnavailableError
        await super().save_batch(messages)


class _BlockingOutboxStore(RecordingOutboxStore):
    """Blocks the owner's stage inside ``save_batch`` until cancelled; carries its scope's UoW.

    The blocked store publishes itself so the cancellation test can assert on the exact owner UoW —
    the relay resolves its own sibling instances (it only ``fetch``es, never ``save_batch``es, so it
    never blocks and never joins ``blocked``).
    """

    def __init__(self, entered: anyio.Event, release: anyio.Event, blocked: list[_BlockingOutboxStore]) -> None:
        super().__init__()
        self.uow = RecordingUoW()
        self._entered = entered
        self._release = release
        self._blocked = blocked

    @override
    async def save_batch(self, messages: Sequence[OutboxMessage]) -> None:
        self._blocked.append(self)
        self._entered.set()
        await self._release.wait()


def _uow_from_blocking_store(store: IOutboxStore) -> IUnitOfWork:
    return cast('_BlockingOutboxStore', store).uow


def _fresh_uow() -> RecordingUoW:
    return RecordingUoW()


def _durability(unit_of_work: IUnitOfWork, outbox: IOutboxStore) -> IDurabilityStore:
    return RecordingDurabilityStore(
        unit_of_work=unit_of_work,
        outbox=outbox,
        inbox=FakeInboxStore(),
        dead_letters=RecordingDeadLetterStore(),
    )


def _outbox_providers(uow: RecordingUoW, store: IOutboxStore, sink: _SentSink) -> list[Provider]:
    return [
        object_(uow, provided_type=IUnitOfWork),
        object_(store, provided_type=IOutboxStore),
        object_(sink, provided_type=_SentSink),
        scoped(IDurabilityStore, _durability),
    ]


def _external_config(*uris: str) -> MessagingConfig:
    return MessagingConfig(
        endpoints=[external_endpoint(uri) for uri in uris],
        routing=[route(_OrderPlaced).to(uri) for uri in uris],
        outbox=OutboxConfig(),
        transports={uri.split('://', 1)[0]: RecordingTransport for uri in uris},
        observers=[_SentObserver],
    )


def _mixed_config() -> MessagingConfig:
    # One outbox-backed destination and one non-durable local queue for the same message type.
    return MessagingConfig(
        endpoints=[external_endpoint('test://events'), local_queue('local-q')],
        routing=[route(_OrderPlaced).to('test://events'), route(_OrderPlaced).to('local-q')],
        outbox=OutboxConfig(),
        transports={'test': RecordingTransport},
        observers=[_SentObserver],
    )


@asynccontextmanager
async def _running_bus(
    config: MessagingConfig,
    *,
    providers: Sequence[Provider],
    handlers: MessagingExtension,
) -> AsyncGenerator[IMessageBus]:
    async with (
        create_test_app(
            imports=[MessagingModule.register(config)],
            extensions=[handlers],
            providers=providers,
        ) as app,
        app.container() as container,
    ):
        yield await container.get(IMessageBus)


# The direct-send/publish ownership contract (CRIT2.1). ``sent`` is fired only by the owner and only
# after the durable commit, so its presence is the machine-immune proof of a committed stage; the
# background relay/maintenance pollers commit the shared UoW on their own ticks (so ``commit_count`` is
# not owner-specific) but never roll back and never emit ``sent``.
class TestDirectSendOwnership:
    @staticmethod
    @pytest.mark.parametrize('verb', ['send', 'publish'])
    async def test_direct_dispatch_to_outbox_stages_and_fires_sent_after_commit(verb: str) -> None:
        uow, store, sink = RecordingUoW(), RecordingOutboxStore(), _SentSink()

        async with _running_bus(
            _external_config('test://events'),
            providers=_outbox_providers(uow, store, sink),
            handlers=MessagingExtension().bind(_ExternalRoutedHandler),
        ) as bus:
            await getattr(bus, verb)(_OrderPlaced(order_id='456'))

        # This guards staging + owner-fired `sent`, not commit-ordering: with recording doubles the sink
        # is not gated on commit success, so reordering `sent` before the commit would still pass here.
        # The commit-before-`sent` ordering law is pinned by the cancellation test (empty sink after
        # rollback) and the real-backend teardown test (row survives only if committed).
        assert len(store.saved) == 1
        assert store.saved[0].destination == 'test://events'
        assert sink.destinations == ['test://events']

    @staticmethod
    async def test_failing_outbox_stage_raises_rolls_back_and_fires_no_sent() -> None:
        uow, store, sink = RecordingUoW(), _FailingOutboxStore(), _SentSink()
        _LocalOrderHandler.received.clear()

        async with _running_bus(
            _mixed_config(),
            providers=_outbox_providers(uow, store, sink),
            handlers=MessagingExtension().bind(_LocalOrderHandler),
        ) as bus:
            with pytest.raises(_OutboxUnavailableError):
                await bus.publish(_OrderPlaced(order_id='fail'))

        assert uow.rollback_count == 1  # the owner rolled back (pollers never roll back)
        assert sink.destinations == []  # no evidence without a commit
        assert _LocalOrderHandler.received == []  # the non-outbox sibling was never dispatched

    @staticmethod
    async def test_fanout_to_two_outbox_endpoints_stages_and_commits_both() -> None:
        uow, store, sink = RecordingUoW(), RecordingOutboxStore(), _SentSink()

        async with _running_bus(
            _external_config('ta://events', 'tb://events'),
            providers=_outbox_providers(uow, store, sink),
            handlers=MessagingExtension().bind(_ExternalRoutedHandler),
        ) as bus:
            await bus.publish(_OrderPlaced(order_id='x'))

        assert {row.destination for row in store.saved} == {'ta://events', 'tb://events'}
        assert set(sink.destinations) == {'ta://events', 'tb://events'}  # one owner tx, both committed

    @staticmethod
    async def test_fanout_partial_failure_rolls_back_and_fires_no_sent() -> None:
        uow, store, sink = RecordingUoW(), _FailOnSecondOutboxStore(), _SentSink()

        async with _running_bus(
            _external_config('ta://events', 'tb://events'),
            providers=_outbox_providers(uow, store, sink),
            handlers=MessagingExtension().bind(_ExternalRoutedHandler),
        ) as bus:
            with pytest.raises(_OutboxUnavailableError):
                await bus.publish(_OrderPlaced(order_id='partial'))

        assert uow.rollback_count == 1  # a single owner tx spans both destinations, rolled back once
        assert sink.destinations == []  # neither destination's sent fired

    @staticmethod
    async def test_mixed_fanout_stages_outbox_and_dispatches_local() -> None:
        uow, store, sink = RecordingUoW(), RecordingOutboxStore(), _SentSink()
        _LocalOrderHandler.received.clear()

        async with _running_bus(
            _mixed_config(),
            providers=_outbox_providers(uow, store, sink),
            handlers=MessagingExtension().bind(_LocalOrderHandler),
        ) as bus:
            await bus.publish(_OrderPlaced(order_id='mixed'))
            await wait_until(lambda: _LocalOrderHandler.received == ['mixed'])

        assert len(store.saved) == 1  # only the outbox-backed destination is staged
        assert 'test://events' in sink.destinations  # outbox endpoint committed and fired sent
        assert _LocalOrderHandler.received == ['mixed']  # local sibling dispatched on the ambient scope

    @staticmethod
    @pytest.mark.parametrize('verb', ['local-send', 'empty-publish', 'invoke'])
    async def test_no_owner_path_opens_no_transaction(verb: str) -> None:
        # Each no-owner verb is isolated so a single regressed path fails under its own test id.
        # `local-send` and `empty-publish` are the live guards (an errant owner filter would run over the
        # shared object_ UoW and flip commit_count to 1); `invoke` never reaches the ownership path at all,
        # kept as explicit documentation that the inline request path stays owner-free.
        uow = RecordingUoW()
        _LocalOrderHandler.received.clear()

        config = MessagingConfig(
            endpoints=[local_queue('local-q')],
            routing=[route(_OrderPlaced).to('local-q')],
        )

        async with _running_bus(
            config,
            providers=[object_(uow, provided_type=IUnitOfWork)],
            handlers=MessagingExtension().bind(_LocalOrderHandler).bind(_PingHandler),
        ) as bus:
            if verb == 'local-send':
                await bus.send(_OrderPlaced(order_id='local'))  # local-only route: no owner
                await wait_until(lambda: _LocalOrderHandler.received == ['local'])
            elif verb == 'empty-publish':
                await bus.publish(_Unrouted())  # zero subscribers: no owner
            else:
                await bus.invoke(_Ping(name='q'))  # inline request: no owner

        assert uow.commit_count == 0
        assert uow.rollback_count == 0

    @staticmethod
    async def test_in_handler_direct_send_commits_isolated_from_handler_rollback() -> None:
        # Send-now (D-CRIT-1): a direct send inside a handler commits its own APP-scope tx even when the
        # handler later rolls back. The owner must NOT enlist the ambient request scope — if it resolved
        # `self._container`, the handler's REQUEST-scoped UoW would be eagerly committed here (commit_count
        # would be 1 before the raise), which this test forbids.
        outbox, sink = RecordingOutboxStore(), _SentSink()
        handler_uows: list[RecordingUoW] = []

        class _SendThenRaiseHandler(RequestHandler[_Ping, None]):
            def __init__(self, bus: IMessageBus, uow: IUnitOfWork) -> None:
                self._bus = bus
                self._uow = uow

            @override
            async def handle(self, request: _Ping, /) -> None:
                handler_uows.append(cast('RecordingUoW', self._uow))
                await self._bus.send(_OrderPlaced(order_id=request.name))  # owner commits + fires sent
                msg = 'handler boom'
                raise RuntimeError(msg)

        config = MessagingConfig(
            endpoints=[external_endpoint('test://events')],
            routing=[route(_OrderPlaced).to('test://events')],
            outbox=OutboxConfig(),
            transports={'test': RecordingTransport},
            observers=[_SentObserver],
            global_pipeline_behaviors=[TransactionalBehavior],
        )

        async with _running_bus(
            config,
            providers=[
                scoped(IUnitOfWork, _fresh_uow),
                object_(outbox, provided_type=IOutboxStore),
                object_(sink, provided_type=_SentSink),
                scoped(IDurabilityStore, _durability),
            ],
            handlers=MessagingExtension().bind(_SendThenRaiseHandler).bind(_ExternalRoutedHandler),
        ) as bus:
            with pytest.raises(RuntimeError, match='handler boom'):
                await bus.invoke(_Ping(name='iso'))

        handler_uow = handler_uows[0]
        assert len(outbox.saved) == 1  # Y staged by the owner
        assert sink.destinations == ['test://events']  # owner committed Y and fired sent (send-now)
        assert handler_uow.commit_count == 0  # the bus owner never enlisted the ambient handler scope
        assert handler_uow.rollback_count == 1  # the handler's own tx rolled back

    @staticmethod
    async def test_cancelled_direct_send_rolls_back_owner_and_fires_no_sent() -> None:
        # Cancellation follows the transaction substrate law verbatim: the owner UoW rolls back and never
        # commits, and no `sent` evidence is emitted.
        sink = _SentSink()
        entered = anyio.Event()
        release = anyio.Event()  # never set — the stage stays blocked until cancelled
        blocked: list[_BlockingOutboxStore] = []

        def _make_blocking_store() -> IOutboxStore:
            return _BlockingOutboxStore(entered, release, blocked)

        async with (
            _running_bus(
                _external_config('test://events'),
                providers=[
                    scoped(IOutboxStore, _make_blocking_store),
                    scoped(IUnitOfWork, _uow_from_blocking_store),
                    object_(sink, provided_type=_SentSink),
                    scoped(IDurabilityStore, _durability),
                ],
                handlers=MessagingExtension().bind(_ExternalRoutedHandler),
            ) as bus,
            anyio.create_task_group() as tg,
        ):

            async def _send() -> None:
                await bus.send(_OrderPlaced(order_id='cancel'))

            tg.start_soon(_send)
            await entered.wait()
            tg.cancel_scope.cancel()

        assert len(blocked) == 1  # exactly the owner's stage blocked (the relay only fetches, never blocks)
        owner_uow = blocked[0].uow
        assert owner_uow.rollback_count == 1  # cancellation rolled the owner tx back
        assert owner_uow.commit_count == 0  # it never committed
        assert sink.destinations == []  # no evidence without a commit

    @staticmethod
    async def test_direct_send_committed_row_survives_teardown_on_real_backend() -> None:
        # CRIT2.1 headline pin on the REAL memory backend, whose IOutboxStore is workspace-backed: an
        # uncommitted stage is discarded when the transactional workspace tears down. Pre-fix the direct
        # send stages but never commits, so exiting the scope drops the row (zero committed). The owner's
        # synchronous commit is what makes the staged row survive teardown — proven here against the real
        # backend rather than a recording double that would append regardless of commit. A fresh scope
        # reads a snapshot of committed state; the row count is status-invariant (the background relay only
        # transitions its status, never removes a committed row), so exactly one row proves the commit.
        config = MessagingConfig(
            endpoints=[external_endpoint('test://events')],
            routing=[route(_OrderPlaced).to('test://events')],
            outbox=OutboxConfig(),
            transports={'test': RecordingTransport},
        )

        async with create_test_app(
            base=MemoryBackend.register(),
            imports=[MessagingModule.register(config)],
            extensions=[MessagingExtension().bind(_ExternalRoutedHandler)],
        ) as app:
            async with app.container() as scope:
                bus = await scope.get(IMessageBus)
                await bus.send(_OrderPlaced(order_id='survives-teardown'))
            # The send scope has torn down; a pre-fix (never-committed) stage would now be discarded.
            async with app.container() as verify_scope:
                outbox = await verify_scope.get(IOutboxStore)
                committed = [row.destination for row in cast('WorkspaceOutboxStore', outbox).messages]

        assert committed == ['test://events']  # committed by the owner, survived teardown
