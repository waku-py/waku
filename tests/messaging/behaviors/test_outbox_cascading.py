from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

import anyio
import pytest
from typing_extensions import override

from waku.di import object_
from waku.messaging import (
    EventHandler,
    IEvent,
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
from waku.testing import create_test_app
from waku.uow import IUnitOfWork

from tests.messaging.helpers import FakeUoW, RecordingTransport
from tests.messaging.outbox.fake_store import FakeOutboxStore

if TYPE_CHECKING:
    from collections.abc import Sequence

    from waku.messaging.outbox.models import OutboxMessage


@dataclass(frozen=True, kw_only=True)
class _PlaceOrder(IRequest[None]):
    order_id: str


@dataclass(frozen=True, kw_only=True)
class _OrderShipped(IEvent):  # routed to external_endpoint -> durable -> outbox in-tx
    order_id: str


@dataclass(frozen=True, kw_only=True)
class _OrderLogged(IEvent):  # routed to local_queue -> non-durable -> deferred post-commit
    order_id: str


class _NoopShippedHandler(EventHandler[_OrderShipped]):
    # Bound only to satisfy the router's "route() needs handlers" check; an ExternalEndpoint
    # writes to the outbox and never invokes this handler.
    @override
    async def handle(self, event: _OrderShipped, /) -> None: ...  # pragma: no cover


class _FailingOutboxStore(FakeOutboxStore):
    @override
    async def save_batch(self, messages: Sequence[OutboxMessage]) -> None:
        msg = 'outbox down'
        raise ConnectionError(msg)


def _config(outbox_store: FakeOutboxStore) -> MessagingConfig:
    return MessagingConfig(
        endpoints=[external_endpoint('ext://shipped'), local_queue('local://logged')],
        routing=[route(_OrderShipped).to('ext://shipped'), route(_OrderLogged).to('local://logged')],
        outbox=OutboxConfig(store=lambda: outbox_store, transport=RecordingTransport),
        global_pipeline_behaviors=[TransactionalBehavior],
    )


class TestOutboxCascadingPerDestination:
    @staticmethod
    async def test_durable_cascade_writes_to_outbox_in_handler_transaction() -> None:
        outbox = FakeOutboxStore()
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
                imports=[MessagingModule.register(_config(outbox))],
                extensions=[
                    MessagingExtension()
                    .bind(_PlaceOrder, PlaceOrderHandler)
                    .bind(_OrderShipped, _NoopShippedHandler)
                    .bind(_OrderLogged, LoggedHandler),
                ],
                providers=[object_(FakeUoW(), provided_type=IUnitOfWork)],
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
        outbox = FakeOutboxStore()
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
                imports=[MessagingModule.register(_config(outbox))],
                extensions=[
                    MessagingExtension()
                    .bind(_PlaceOrder, PlaceOrderHandler)
                    .bind(_OrderShipped, _NoopShippedHandler)
                    .bind(_OrderLogged, LoggedHandler),
                ],
                providers=[object_(FakeUoW(), provided_type=IUnitOfWork)],
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
        outbox = FakeOutboxStore()
        uow = FakeUoW()

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
                imports=[MessagingModule.register(_config(outbox))],
                extensions=[
                    MessagingExtension()
                    .bind(_PlaceOrder, FailingHandler)
                    .bind(_OrderShipped, _NoopShippedHandler)
                    .bind(_OrderLogged, LoggedHandler),
                ],
                providers=[object_(uow, provided_type=IUnitOfWork)],
            ) as app,
            app.container() as container,
        ):
            bus = await container.get(IMessageBus)
            with pytest.raises(RuntimeError, match='boom'):
                await bus.invoke(_PlaceOrder(order_id='o-3'))

        # The cascade frame is drained only AFTER the handler succeeds, so a handler failure
        # never writes the durable cascade nor flushes the deferred one; the tx rolls back.
        # (`committed` is not asserted: the background outbox relay commits the shared FakeUoW.)
        assert outbox.saved == []
        assert uow.rolled_back is True

    @staticmethod
    async def test_durable_dispatch_failure_rolls_back_handler() -> None:
        outbox = _FailingOutboxStore()
        uow = FakeUoW()

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
                imports=[MessagingModule.register(_config(outbox))],
                extensions=[
                    MessagingExtension()
                    .bind(_PlaceOrder, PlaceOrderHandler)
                    .bind(_OrderShipped, _NoopShippedHandler)
                    .bind(_OrderLogged, LoggedHandler),
                ],
                providers=[object_(uow, provided_type=IUnitOfWork)],
            ) as app,
            app.container() as container,
        ):
            bus = await container.get(IMessageBus)
            with pytest.raises(ConnectionError, match='outbox down'):
                await bus.invoke(_PlaceOrder(order_id='o-4'))

        # OutboxCascadingBehavior re-raised the durable-write failure -> TransactionalBehavior rolled back.
        # (`committed` is not asserted: the background outbox relay commits the shared FakeUoW.)
        assert uow.rolled_back is True
