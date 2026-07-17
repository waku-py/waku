from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

import anyio
import pytest
from typing_extensions import override

from waku._internal.transaction import AfterCommitError, TransactionExecutionError
from waku.di import object_, scoped
from waku.messages import IEvent
from waku.messaging import (
    EndpointMode,
    EventHandler,
    IMessageBus,
    IOutgoingMessages,
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
from waku.messaging.endpoints import EndpointEntry
from waku.messaging.endpoints._internal.local_queue import LocalQueueEndpoint
from waku.testing import create_test_app
from waku.uow import IUnitOfWork

from tests.messaging.helpers import RecordingDeadLetterStore, RecordingDurabilityStore, RecordingTransport, RecordingUoW
from tests.messaging.inbox.fake_store import FakeInboxStore
from tests.messaging.outbox.fake_store import RecordingOutboxStore

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pytest_mock import MockerFixture

    from waku.messaging.outbox.models import OutboxMessage

_CASCADE_LOGGER = 'waku.messaging._internal.outbox_cascading'


@dataclass(frozen=True, kw_only=True)
class _PlaceOrder(IRequest[None]):
    order_id: str


@dataclass(frozen=True, kw_only=True)
class _OrderShipped(IEvent):  # routed to external_endpoint -> durable -> outbox in-tx
    order_id: str


@dataclass(frozen=True, kw_only=True)
class _OrderLogged(IEvent):  # routed to local_queue -> non-durable -> deferred post-commit
    order_id: str


@dataclass(frozen=True, kw_only=True)
class _OrderMixed(IEvent):  # routed to BOTH an inline local_queue AND external_endpoint
    order_id: str


@dataclass(frozen=True)
class _UnroutedPing(IRequest[None]):  # no route, no handler -> cascaded send drops (BC-27.1)
    pass


class _NoopShippedHandler(EventHandler[_OrderShipped]):
    # Bound only to satisfy the router's "route() needs handlers" check; an ExternalEndpoint
    # writes to the outbox and never invokes this handler.
    @override
    async def handle(self, event: _OrderShipped, /) -> None: ...  # pragma: no cover


class _NoopLoggedHandler(EventHandler[_OrderLogged]):
    # Bound by mixed-cascade tests that never publish _OrderLogged, only to satisfy its route.
    @override
    async def handle(self, event: _OrderLogged, /) -> None: ...  # pragma: no cover


class _FailingOutboxStore(RecordingOutboxStore):
    @override
    async def save_batch(self, messages: Sequence[OutboxMessage]) -> None:
        msg = 'outbox down'
        raise ConnectionError(msg)


def _fresh_uow() -> RecordingUoW:
    return RecordingUoW()


def _durability(unit_of_work: IUnitOfWork, outbox: IOutboxStore) -> IDurabilityStore:
    return RecordingDurabilityStore(
        unit_of_work=unit_of_work,
        outbox=outbox,
        inbox=FakeInboxStore(),
        dead_letters=RecordingDeadLetterStore(),
    )


def _config(*, mixed: bool = False) -> MessagingConfig:
    endpoints: list[EndpointEntry] = [external_endpoint('ext://shipped'), local_queue('local://logged')]
    routing = [route(_OrderShipped).to('ext://shipped'), route(_OrderLogged).to('local://logged')]
    if mixed:
        # INLINE mode: the non-durable leg runs synchronously wherever it is dispatched from, so
        # the tests observe deterministically WHEN it runs relative to the handler's commit. The
        # non-durable route comes FIRST — a full-fanout regression would hit it before the outbox.
        endpoints.append(local_queue('local://mixed', mode=EndpointMode.INLINE))
        routing.extend([route(_OrderMixed).to('local://mixed'), route(_OrderMixed).to('ext://shipped')])
    return MessagingConfig(
        endpoints=endpoints,
        routing=routing,
        outbox=OutboxConfig(),
        transports={'ext': RecordingTransport},
        global_pipeline_behaviors=[TransactionalBehavior],
    )


def _endpoint_config() -> MessagingConfig:
    return MessagingConfig(
        endpoints=[local_queue('local://orders'), local_queue('local://logged')],
        routing=[route(_PlaceOrder).to('local://orders'), route(_OrderLogged).to('local://logged')],
        global_pipeline_behaviors=[TransactionalBehavior],
    )


class TestOutboxCascadingPerDestination:
    @staticmethod
    async def test_endpoint_owner_flushes_non_durable_cascade_after_commit() -> None:
        trace: list[str] = []
        logged_done = asyncio.Event()

        class TracingUoW(IUnitOfWork):
            @override
            async def commit(self) -> None:
                trace.append('commit')

            @override
            async def rollback(self) -> None:  # pragma: no cover - success path invariant
                trace.append('rollback')

        class PlaceOrderHandler(RequestHandler[_PlaceOrder, None]):
            def __init__(self, outgoing: IOutgoingMessages) -> None:
                self._outgoing = outgoing

            @override
            async def handle(self, cmd: _PlaceOrder, /) -> None:
                self._outgoing.publish(_OrderLogged(order_id=cmd.order_id))

        class LoggedHandler(EventHandler[_OrderLogged]):
            @override
            async def handle(self, _event: _OrderLogged, /) -> None:
                trace.append('cascade')
                logged_done.set()

        async with (
            create_test_app(
                imports=[MessagingModule.register(_endpoint_config())],
                extensions=[MessagingExtension().bind(PlaceOrderHandler).bind(LoggedHandler)],
                providers=[object_(TracingUoW(), provided_type=IUnitOfWork)],
            ) as app,
            app.container() as container,
        ):
            bus = await container.get(IMessageBus)
            await bus.send(_PlaceOrder(order_id='endpoint-success'))
            with anyio.fail_after(5):
                await logged_done.wait()

        assert trace[:2] == ['commit', 'cascade']

    @staticmethod
    async def test_endpoint_owner_does_not_flush_non_durable_cascade_after_rollback() -> None:
        trace: list[str] = []
        rollback_done = asyncio.Event()
        cascade_ran = asyncio.Event()

        class TracingUoW(IUnitOfWork):
            @override
            async def commit(self) -> None:  # pragma: no cover - failure path invariant
                trace.append('commit')

            @override
            async def rollback(self) -> None:
                trace.append('rollback')
                rollback_done.set()

        class FailingHandler(RequestHandler[_PlaceOrder, None]):
            def __init__(self, outgoing: IOutgoingMessages) -> None:
                self._outgoing = outgoing

            @override
            async def handle(self, cmd: _PlaceOrder, /) -> None:
                self._outgoing.publish(_OrderLogged(order_id=cmd.order_id))
                msg = 'handler failed'
                raise RuntimeError(msg)

        class LoggedHandler(EventHandler[_OrderLogged]):
            @override
            async def handle(self, _event: _OrderLogged, /) -> None:  # pragma: no cover - invariant guard
                cascade_ran.set()

        async with (
            create_test_app(
                imports=[MessagingModule.register(_endpoint_config())],
                extensions=[MessagingExtension().bind(FailingHandler).bind(LoggedHandler)],
                providers=[object_(TracingUoW(), provided_type=IUnitOfWork)],
            ) as app,
            app.container() as container,
        ):
            bus = await container.get(IMessageBus)
            await bus.send(_PlaceOrder(order_id='endpoint-failure'))
            with anyio.fail_after(5):
                await rollback_done.wait()
            with anyio.move_on_after(0.05):
                await cascade_ran.wait()

        assert trace == ['rollback']
        assert not cascade_ran.is_set()

    @staticmethod
    async def test_durable_cascade_writes_to_outbox_in_handler_transaction() -> None:
        outbox = RecordingOutboxStore()
        logged_done = asyncio.Event()

        class PlaceOrderHandler(RequestHandler[_PlaceOrder, None]):
            def __init__(self, outgoing: IOutgoingMessages) -> None:
                self._outgoing = outgoing

            @override
            async def handle(self, cmd: _PlaceOrder, /) -> None:
                self._outgoing.publish(_OrderShipped(order_id=cmd.order_id))  # durable

        class LoggedHandler(EventHandler[_OrderLogged]):
            @override
            async def handle(self, event: _OrderLogged, /) -> None:  # pragma: no cover
                logged_done.set()

        async with (
            create_test_app(
                imports=[MessagingModule.register(_config())],
                extensions=[
                    MessagingExtension().bind(PlaceOrderHandler).bind(_NoopShippedHandler).bind(LoggedHandler),
                ],
                providers=[
                    object_(RecordingUoW(), provided_type=IUnitOfWork),
                    object_(outbox, provided_type=IOutboxStore),
                    scoped(IDurabilityStore, _durability),
                ],
            ) as app,
            app.container() as container,
        ):
            bus = await container.get(IMessageBus)
            await bus.invoke(_PlaceOrder(order_id='o-1'))

        # Durable cascade was written to the outbox (one row), not deferred.
        assert len(outbox.saved) == 1
        assert outbox.saved[0].destination == 'ext://shipped'

    @staticmethod
    async def test_non_durable_cascade_flushed_post_commit_via_deferred_bucket() -> None:
        outbox = RecordingOutboxStore()
        logged: list[str] = []
        logged_done = asyncio.Event()

        class PlaceOrderHandler(RequestHandler[_PlaceOrder, None]):
            def __init__(self, outgoing: IOutgoingMessages) -> None:
                self._outgoing = outgoing

            @override
            async def handle(self, cmd: _PlaceOrder, /) -> None:
                self._outgoing.publish(_OrderLogged(order_id=cmd.order_id))  # non-durable

        class LoggedHandler(EventHandler[_OrderLogged]):
            @override
            async def handle(self, event: _OrderLogged, /) -> None:
                logged.append(event.order_id)
                logged_done.set()

        async with (
            create_test_app(
                imports=[MessagingModule.register(_config())],
                extensions=[
                    MessagingExtension().bind(PlaceOrderHandler).bind(_NoopShippedHandler).bind(LoggedHandler),
                ],
                providers=[
                    object_(RecordingUoW(), provided_type=IUnitOfWork),
                    object_(outbox, provided_type=IOutboxStore),
                    scoped(IDurabilityStore, _durability),
                ],
            ) as app,
            app.container() as container,
        ):
            bus = await container.get(IMessageBus)
            await bus.invoke(_PlaceOrder(order_id='o-2'))
            with anyio.fail_after(5):
                await logged_done.wait()

            # Non-durable cascade flushed post-commit via the deferred bucket — NOT in the outbox.
            assert logged == ['o-2']
            assert outbox.saved == []

    @staticmethod
    async def test_handler_rollback_removes_durable_and_skips_deferred() -> None:
        outbox = RecordingOutboxStore()
        uow = RecordingUoW()

        class FailingHandler(RequestHandler[_PlaceOrder, None]):
            def __init__(self, outgoing: IOutgoingMessages) -> None:
                self._outgoing = outgoing

            @override
            async def handle(self, cmd: _PlaceOrder, /) -> None:
                self._outgoing.publish(_OrderShipped(order_id=cmd.order_id))
                self._outgoing.publish(_OrderLogged(order_id=cmd.order_id))
                msg = 'boom'
                raise RuntimeError(msg)

        class LoggedHandler(EventHandler[_OrderLogged]):
            @override
            async def handle(self, event: _OrderLogged, /) -> None:  # pragma: no cover
                pytest.fail('deferred cascade must not flush on handler failure')

        async with (
            create_test_app(
                imports=[MessagingModule.register(_config())],
                extensions=[
                    MessagingExtension().bind(FailingHandler).bind(_NoopShippedHandler).bind(LoggedHandler),
                ],
                providers=[
                    object_(uow, provided_type=IUnitOfWork),
                    object_(outbox, provided_type=IOutboxStore),
                    scoped(IDurabilityStore, _durability),
                ],
            ) as app,
            app.container() as container,
        ):
            bus = await container.get(IMessageBus)
            with pytest.raises(RuntimeError, match='boom'):
                await bus.invoke(_PlaceOrder(order_id='o-3'))

        # The cascade frame is drained only AFTER the handler succeeds, so a handler failure
        # never writes the durable cascade nor flushes the deferred one; the tx rolls back.
        # (`committed` is not asserted: the background outbox relay commits the shared RecordingUoW.)
        assert outbox.saved == []
        assert uow.rolled_back is True

    @staticmethod
    async def test_durable_dispatch_failure_rolls_back_handler() -> None:
        outbox = _FailingOutboxStore()
        uow = RecordingUoW()

        class PlaceOrderHandler(RequestHandler[_PlaceOrder, None]):
            def __init__(self, outgoing: IOutgoingMessages) -> None:
                self._outgoing = outgoing

            @override
            async def handle(self, cmd: _PlaceOrder, /) -> None:
                self._outgoing.publish(_OrderShipped(order_id=cmd.order_id))  # durable -> save fails

        class LoggedHandler(EventHandler[_OrderLogged]):
            @override
            async def handle(self, event: _OrderLogged, /) -> None:  # pragma: no cover
                pytest.fail('deferred cascade must not flush when the durable write fails')

        async with (
            create_test_app(
                imports=[MessagingModule.register(_config())],
                extensions=[
                    MessagingExtension().bind(PlaceOrderHandler).bind(_NoopShippedHandler).bind(LoggedHandler),
                ],
                providers=[
                    object_(uow, provided_type=IUnitOfWork),
                    object_(outbox, provided_type=IOutboxStore),
                    scoped(IDurabilityStore, _durability),
                ],
            ) as app,
            app.container() as container,
        ):
            bus = await container.get(IMessageBus)
            with pytest.raises(ConnectionError, match='outbox down'):
                await bus.invoke(_PlaceOrder(order_id='o-4'))

        # OutboxCascadingBehavior re-raised the durable-write failure -> TransactionalBehavior rolled back.
        # (`committed` is not asserted: the background outbox relay commits the shared RecordingUoW.)
        assert uow.rolled_back is True


class TestMixedDurabilityCascade:
    @staticmethod
    async def test_mixed_cascade_writes_durable_leg_in_tx_and_delivers_non_durable_leg_post_commit() -> None:
        outbox = RecordingOutboxStore()
        seen: dict[str, RecordingUoW] = {}
        delivered_after_commit: list[bool] = []

        class PlaceOrderHandler(RequestHandler[_PlaceOrder, None]):
            def __init__(self, outgoing: IOutgoingMessages, uow: IUnitOfWork) -> None:
                self._outgoing = outgoing
                self._uow = uow

            @override
            async def handle(self, cmd: _PlaceOrder, /) -> None:
                seen['uow'] = cast('RecordingUoW', self._uow)
                self._outgoing.publish(_OrderMixed(order_id=cmd.order_id))

        class MixedHandler(EventHandler[_OrderMixed]):
            @override
            async def handle(self, event: _OrderMixed, /) -> None:
                delivered_after_commit.append(seen['uow'].committed)

        async with (
            create_test_app(
                imports=[MessagingModule.register(_config(mixed=True))],
                extensions=[
                    MessagingExtension()
                    .bind(PlaceOrderHandler)
                    .bind(_NoopShippedHandler)
                    .bind(_NoopLoggedHandler)
                    .bind(MixedHandler),
                ],
                # A FRESH UoW per scope: the shared-instance pattern would let the background
                # outbox relay's commits pollute the `committed` flag this test observes.
                providers=[
                    scoped(IUnitOfWork, _fresh_uow),
                    object_(outbox, provided_type=IOutboxStore),
                    scoped(IDurabilityStore, _durability),
                ],
            ) as app,
            app.container() as container,
        ):
            bus = await container.get(IMessageBus)
            await bus.invoke(_PlaceOrder(order_id='o-5'))

            # The INLINE non-durable leg ran synchronously inside the post-commit deferred flush:
            # exactly once, and strictly AFTER the handler's transaction committed.
            assert delivered_after_commit == [True]
            # The durable leg produced exactly ONE outbox row — the local delivery came from the
            # deferred flush, not from a second outbox-backed dispatch.
            assert [message.destination for message in outbox.saved] == ['ext://shipped']

    @staticmethod
    async def test_mixed_cascade_durable_write_failure_rolls_back_without_non_durable_delivery() -> None:
        outbox = _FailingOutboxStore()
        uow = RecordingUoW()
        delivered: list[str] = []

        class PlaceOrderHandler(RequestHandler[_PlaceOrder, None]):
            def __init__(self, outgoing: IOutgoingMessages) -> None:
                self._outgoing = outgoing

            @override
            async def handle(self, cmd: _PlaceOrder, /) -> None:
                self._outgoing.publish(_OrderMixed(order_id=cmd.order_id))

        class MixedHandler(EventHandler[_OrderMixed]):
            @override
            async def handle(self, event: _OrderMixed, /) -> None:
                delivered.append(event.order_id)

        async with (
            create_test_app(
                imports=[MessagingModule.register(_config(mixed=True))],
                extensions=[
                    MessagingExtension()
                    .bind(PlaceOrderHandler)
                    .bind(_NoopShippedHandler)
                    .bind(_NoopLoggedHandler)
                    .bind(MixedHandler),
                ],
                providers=[
                    object_(uow, provided_type=IUnitOfWork),
                    object_(outbox, provided_type=IOutboxStore),
                    scoped(IDurabilityStore, _durability),
                ],
            ) as app,
            app.container() as container,
        ):
            bus = await container.get(IMessageBus)
            with pytest.raises(ConnectionError, match='outbox down'):
                await bus.invoke(_PlaceOrder(order_id='o-6'))

        # The durable-leg failure rolled the transaction back; the non-durable leg (routed FIRST)
        # must never have been delivered — it stays staged-then-discarded, not dispatched in-tx.
        assert delivered == []
        assert uow.rolled_back is True

    @staticmethod
    async def test_mixed_cascade_handler_failure_discards_both_legs() -> None:
        outbox = RecordingOutboxStore()
        uow = RecordingUoW()
        delivered: list[str] = []

        class FailingHandler(RequestHandler[_PlaceOrder, None]):
            def __init__(self, outgoing: IOutgoingMessages) -> None:
                self._outgoing = outgoing

            @override
            async def handle(self, cmd: _PlaceOrder, /) -> None:
                self._outgoing.publish(_OrderMixed(order_id=cmd.order_id))
                msg = 'boom'
                raise RuntimeError(msg)

        class MixedHandler(EventHandler[_OrderMixed]):
            @override
            async def handle(self, event: _OrderMixed, /) -> None:  # pragma: no cover
                delivered.append(event.order_id)

        async with (
            create_test_app(
                imports=[MessagingModule.register(_config(mixed=True))],
                extensions=[
                    MessagingExtension()
                    .bind(FailingHandler)
                    .bind(_NoopShippedHandler)
                    .bind(_NoopLoggedHandler)
                    .bind(MixedHandler),
                ],
                providers=[
                    object_(uow, provided_type=IUnitOfWork),
                    object_(outbox, provided_type=IOutboxStore),
                    scoped(IDurabilityStore, _durability),
                ],
            ) as app,
            app.container() as container,
        ):
            bus = await container.get(IMessageBus)
            with pytest.raises(RuntimeError, match='boom'):
                await bus.invoke(_PlaceOrder(order_id='o-7'))

        assert outbox.saved == []
        assert delivered == []
        assert uow.rolled_back is True


class TestCascadeEdgeCases:
    @staticmethod
    async def test_unrouted_cascaded_send_drops_with_a_warning_not_an_error(
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        outbox = RecordingOutboxStore()

        class PlaceOrderHandler(RequestHandler[_PlaceOrder, None]):
            def __init__(self, outgoing: IOutgoingMessages) -> None:
                self._outgoing = outgoing

            @override
            async def handle(self, cmd: _PlaceOrder, /) -> None:
                self._outgoing.send(_UnroutedPing())

        class LoggedHandler(EventHandler[_OrderLogged]):
            @override
            async def handle(self, event: _OrderLogged, /) -> None: ...  # pragma: no cover

        async with (
            create_test_app(
                imports=[MessagingModule.register(_config())],
                extensions=[
                    MessagingExtension().bind(PlaceOrderHandler).bind(_NoopShippedHandler).bind(LoggedHandler),
                ],
                providers=[
                    object_(RecordingUoW(), provided_type=IUnitOfWork),
                    object_(outbox, provided_type=IOutboxStore),
                    scoped(IDurabilityStore, _durability),
                ],
            ) as app,
            app.container() as container,
        ):
            bus = await container.get(IMessageBus)
            with caplog.at_level(logging.DEBUG, logger=_CASCADE_LOGGER):
                await bus.invoke(_PlaceOrder(order_id='o-8'))  # BC-27.1: no NoRouteError surfaces

        # A single diagnostic WARNING, not the pre-#27 swallowed-NoRouteError ERROR record.
        records = [record for record in caplog.records if record.name == _CASCADE_LOGGER]
        assert [record.levelno for record in records] == [logging.WARNING]
        assert 'zero destinations' in records[0].getMessage()
        assert outbox.saved == []

    @staticmethod
    async def test_post_commit_non_durable_dispatch_failure_is_fatal_after_commit(
        mocker: MockerFixture,
    ) -> None:
        outbox = RecordingOutboxStore()

        class PlaceOrderHandler(RequestHandler[_PlaceOrder, None]):
            def __init__(self, outgoing: IOutgoingMessages) -> None:
                self._outgoing = outgoing

            @override
            async def handle(self, cmd: _PlaceOrder, /) -> None:
                self._outgoing.publish(_OrderLogged(order_id=cmd.order_id))

        class LoggedHandler(EventHandler[_OrderLogged]):
            @override
            async def handle(self, event: _OrderLogged, /) -> None: ...  # pragma: no cover

        mocker.patch.object(
            LocalQueueEndpoint,
            'dispatch',
            new_callable=mocker.AsyncMock,
            side_effect=RuntimeError('queue unavailable'),
        )

        async with (
            create_test_app(
                imports=[MessagingModule.register(_config())],
                extensions=[
                    MessagingExtension().bind(PlaceOrderHandler).bind(_NoopShippedHandler).bind(LoggedHandler),
                ],
                providers=[
                    object_(RecordingUoW(), provided_type=IUnitOfWork),
                    object_(outbox, provided_type=IOutboxStore),
                    scoped(IDurabilityStore, _durability),
                ],
            ) as app,
            app.container() as container,
        ):
            bus = await container.get(IMessageBus)
            with pytest.raises(TransactionExecutionError) as raised:
                await bus.invoke(_PlaceOrder(order_id='o-9'))

        assert isinstance(raised.value, AfterCommitError)
        assert str(raised.value.error) == 'queue unavailable'
