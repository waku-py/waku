from __future__ import annotations

import contextlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

import pytest
from typing_extensions import override

from waku import UnexpectedRollbackError
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
    local_queue,
    route,
)
from waku.messaging.behaviors.transactional import TransactionalBehavior
from waku.messaging.context import get_message_context
from waku.messaging.durability import IOutboxStore
from waku.messaging.exceptions import HandlerNotFoundError
from waku.testing import create_test_app
from waku.uow import IUnitOfWork

from tests.messaging.helpers import RecordingUoW
from tests.messaging.outbox.fake_store import RecordingOutboxStore

if TYPE_CHECKING:
    from contextlib import AbstractAsyncContextManager

    from waku.application import WakuApplication


@dataclass(frozen=True, kw_only=True)
class _OrderShipped(IEvent):
    order: str


@dataclass(frozen=True, kw_only=True)
class _PlaceOrder(IRequest[None]):
    order: str


@dataclass(frozen=True, kw_only=True)
class _AuditLogged(IEvent):  # routed to an INLINE local_queue -> non-durable cascade leg
    order: str


def _transactional_app(
    uow: RecordingUoW, extension: MessagingExtension
) -> AbstractAsyncContextManager[WakuApplication]:
    return create_test_app(
        providers=[object_(uow, provided_type=IUnitOfWork)],
        imports=[MessagingModule.register(MessagingConfig(global_pipeline_behaviors=[TransactionalBehavior]))],
        extensions=[extension],
    )


def _fresh_uow() -> RecordingUoW:
    return RecordingUoW()


def _cascading_app(extension: MessagingExtension) -> AbstractAsyncContextManager[WakuApplication]:
    # INLINE mode: the non-durable subscriber runs synchronously wherever it is dispatched from, so
    # the tests observe deterministically WHEN it runs relative to the fan-out frame's commit.
    # A FRESH UoW per scope keeps the background outbox relay's commits out of the observed counters.
    config = MessagingConfig(
        endpoints=[local_queue('local://audit', mode=EndpointMode.INLINE)],
        routing=[route(_AuditLogged).to('local://audit')],
        outbox=OutboxConfig(),
        global_pipeline_behaviors=[TransactionalBehavior],
    )
    return create_test_app(
        providers=[scoped(IUnitOfWork, _fresh_uow), scoped(IOutboxStore, RecordingOutboxStore)],
        imports=[MessagingModule.register(config)],
        extensions=[extension],
    )


class TestInvokeEventEndToEnd:
    @staticmethod
    async def test_runs_all_handlers_inline() -> None:
        seen: set[str] = set()

        class NotifyHandler(EventHandler[_OrderShipped]):
            @override
            async def handle(self, event: _OrderShipped, /) -> None:
                seen.add(f'notify:{event.order}')

        class AuditHandler(EventHandler[_OrderShipped]):
            @override
            async def handle(self, event: _OrderShipped, /) -> None:
                seen.add(f'audit:{event.order}')

        async with (
            create_test_app(
                imports=[MessagingModule.register()],
                extensions=[MessagingExtension().bind(NotifyHandler, AuditHandler)],
            ) as app,
            app.container() as container,
        ):
            bus = await container.get(IMessageBus)
            result = await bus.invoke(_OrderShipped(order='o-1'))

        assert result is None
        assert seen == {'notify:o-1', 'audit:o-1'}

    @staticmethod
    async def test_raises_handler_not_found_when_no_handlers() -> None:
        async with (
            create_test_app(imports=[MessagingModule.register()]) as app,
            app.container() as container,
        ):
            bus = await container.get(IMessageBus)
            with pytest.raises(HandlerNotFoundError, match='_OrderShipped'):
                await bus.invoke(_OrderShipped(order='o-2'))

    @staticmethod
    async def test_nested_invoke_inherits_causation_chain() -> None:
        captured: dict[str, object] = {}

        class ShippedHandler(EventHandler[_OrderShipped]):
            def __init__(self, bus: IMessageBus) -> None:
                self._bus = bus

            @override
            async def handle(self, event: _OrderShipped, /) -> None:
                ctx = get_message_context()
                captured['parent_message_id'] = ctx.message_id
                captured['parent_correlation_id'] = ctx.correlation_id
                await self._bus.invoke(_PlaceOrder(order=event.order))

        class PlaceOrderHandler(RequestHandler[_PlaceOrder, None]):
            @override
            async def handle(self, request: _PlaceOrder, /) -> None:
                ctx = get_message_context()
                captured['child_causation_id'] = ctx.causation_id
                captured['child_correlation_id'] = ctx.correlation_id
                captured['child_message_id'] = ctx.message_id

        async with (
            create_test_app(
                imports=[MessagingModule.register()],
                extensions=[
                    MessagingExtension().bind(ShippedHandler).bind(PlaceOrderHandler),
                ],
            ) as app,
            app.container() as container,
        ):
            bus = await container.get(IMessageBus)
            await bus.invoke(_OrderShipped(order='o-3'))

        assert captured['child_causation_id'] == str(captured['parent_message_id'])
        assert captured['child_correlation_id'] == captured['parent_correlation_id']
        assert captured['child_message_id'] != captured['parent_message_id']


class TestInvokeEventAtomicity:
    @staticmethod
    async def test_fan_out_commits_once_over_all_handlers() -> None:
        uow = RecordingUoW()
        seen: set[str] = set()

        class HandlerA(EventHandler[_OrderShipped]):
            @override
            async def handle(self, event: _OrderShipped, /) -> None:
                seen.add('a')

        class HandlerB(EventHandler[_OrderShipped]):
            @override
            async def handle(self, event: _OrderShipped, /) -> None:
                seen.add('b')

        async with (
            _transactional_app(uow, MessagingExtension().bind(HandlerA, HandlerB)) as app,
            app.container() as container,
        ):
            bus = await container.get(IMessageBus)
            await bus.invoke(_OrderShipped(order='o-4'))

        assert seen == {'a', 'b'}
        # One commit over BOTH handlers — the dispatcher owns the single transaction frame.
        assert uow.commit_count == 1
        assert uow.rollback_count == 0

    @staticmethod
    async def test_handler_failure_rolls_back_once_and_does_not_commit() -> None:
        uow = RecordingUoW()

        class OkHandler(EventHandler[_OrderShipped]):
            @override
            async def handle(self, event: _OrderShipped, /) -> None: ...

        class BoomHandler(EventHandler[_OrderShipped]):
            @override
            async def handle(self, event: _OrderShipped, /) -> None:
                msg = 'boom'
                raise RuntimeError(msg)

        async with (
            _transactional_app(uow, MessagingExtension().bind(OkHandler, BoomHandler)) as app,
            app.container() as container,
        ):
            bus = await container.get(IMessageBus)
            with pytest.raises(RuntimeError, match='boom'):
                await bus.invoke(_OrderShipped(order='o-5'))

        assert uow.commit_count == 0
        assert uow.rollback_count == 1

    @staticmethod
    async def test_nested_invoke_joins_same_transaction() -> None:
        uow = RecordingUoW()

        class ShippedHandler(EventHandler[_OrderShipped]):
            def __init__(self, bus: IMessageBus) -> None:
                self._bus = bus

            @override
            async def handle(self, event: _OrderShipped, /) -> None:
                await self._bus.invoke(_PlaceOrder(order=event.order))

        class AuditShippedHandler(EventHandler[_OrderShipped]):
            @override
            async def handle(self, event: _OrderShipped, /) -> None: ...

        class PlaceOrderHandler(RequestHandler[_PlaceOrder, None]):
            @override
            async def handle(self, request: _PlaceOrder, /) -> None: ...

        async with (
            _transactional_app(
                uow,
                MessagingExtension().bind(ShippedHandler, AuditShippedHandler).bind(PlaceOrderHandler),
            ) as app,
            app.container() as container,
        ):
            bus = await container.get(IMessageBus)
            await bus.invoke(_OrderShipped(order='o-6'))

        # Two event handlers + the nested request all join ONE transaction — one commit, not three.
        assert uow.commit_count == 1
        assert uow.rollback_count == 0

    @staticmethod
    async def test_nested_invoke_event_defers_non_durable_cascade_past_the_outer_commit() -> None:
        seen: dict[str, RecordingUoW] = {}
        commits_when_subscriber_ran: list[int] = []

        class ShippedHandler(EventHandler[_OrderShipped]):
            def __init__(self, bus: IMessageBus) -> None:
                self._bus = bus

            @override
            async def handle(self, event: _OrderShipped, /) -> None:
                await self._bus.invoke(_PlaceOrder(order=event.order))

        class PlaceOrderHandler(RequestHandler[_PlaceOrder, None]):
            def __init__(self, outgoing: IOutgoingMessages, uow: IUnitOfWork) -> None:
                self._outgoing = outgoing
                self._uow = uow

            @override
            async def handle(self, request: _PlaceOrder, /) -> None:
                seen['uow'] = cast('RecordingUoW', self._uow)
                self._outgoing.publish(_AuditLogged(order=request.order))  # non-durable cascade

        class AuditSubscriber(EventHandler[_AuditLogged]):
            @override
            async def handle(self, event: _AuditLogged, /) -> None:
                commits_when_subscriber_ran.append(seen['uow'].commit_count)

        async with (
            _cascading_app(
                MessagingExtension().bind(ShippedHandler).bind(PlaceOrderHandler).bind(AuditSubscriber),
            ) as app,
            app.container() as container,
        ):
            bus = await container.get(IMessageBus)
            await bus.invoke(_OrderShipped(order='o-7'))

        # The INLINE subscriber ran exactly once, strictly AFTER the fan-out frame's single commit —
        # never from within the still-open transaction (a causal ordering, not a wall-clock race).
        assert commits_when_subscriber_ran == [1]

    @staticmethod
    async def test_rollback_only_discards_non_durable_cascade_and_raises_unexpected_rollback() -> None:
        seen: dict[str, RecordingUoW] = {}
        audit_calls: list[str] = []

        class ShippedHandler(EventHandler[_OrderShipped]):
            def __init__(self, bus: IMessageBus, outgoing: IOutgoingMessages, uow: IUnitOfWork) -> None:
                self._bus = bus
                self._outgoing = outgoing
                self._uow = uow

            @override
            async def handle(self, event: _OrderShipped, /) -> None:
                seen['uow'] = cast('RecordingUoW', self._uow)
                with contextlib.suppress(ValueError):
                    await self._bus.invoke(_PlaceOrder(order=event.order))
                self._outgoing.publish(_AuditLogged(order=event.order))

        class PlaceOrderHandler(RequestHandler[_PlaceOrder, None]):
            @override
            async def handle(self, request: _PlaceOrder, /) -> None:
                msg = 'nested failed'
                raise ValueError(msg)

        class AuditSubscriber(EventHandler[_AuditLogged]):
            @override
            async def handle(self, event: _AuditLogged, /) -> None:
                audit_calls.append(event.order)

        async with (
            _cascading_app(
                MessagingExtension().bind(ShippedHandler).bind(PlaceOrderHandler).bind(AuditSubscriber),
            ) as app,
            app.container() as container,
        ):
            bus = await container.get(IMessageBus)
            with pytest.raises(UnexpectedRollbackError):
                await bus.invoke(_OrderShipped(order='rollback-only'))

        assert audit_calls == []
        assert seen['uow'].commit_count == 0
        assert seen['uow'].rollback_count == 1

    @staticmethod
    async def test_nested_invoke_event_rollback_discards_the_non_durable_cascade() -> None:
        seen: dict[str, RecordingUoW] = {}

        class ShippedHandler(EventHandler[_OrderShipped]):
            def __init__(self, bus: IMessageBus) -> None:
                self._bus = bus

            @override
            async def handle(self, event: _OrderShipped, /) -> None:
                await self._bus.invoke(_PlaceOrder(order=event.order))

        class FailingAuditShippedHandler(EventHandler[_OrderShipped]):
            @override
            async def handle(self, event: _OrderShipped, /) -> None:
                msg = 'sibling boom'
                raise RuntimeError(msg)

        class PlaceOrderHandler(RequestHandler[_PlaceOrder, None]):
            def __init__(self, outgoing: IOutgoingMessages, uow: IUnitOfWork) -> None:
                self._outgoing = outgoing
                self._uow = uow

            @override
            async def handle(self, request: _PlaceOrder, /) -> None:
                seen['uow'] = cast('RecordingUoW', self._uow)
                self._outgoing.publish(_AuditLogged(order=request.order))  # staged, then rolled back

        class AuditSubscriber(EventHandler[_AuditLogged]):
            @override
            async def handle(self, event: _AuditLogged, /) -> None:  # pragma: no cover
                pytest.fail('non-durable cascade must not flush when the fan-out frame rolls back')

        async with (
            _cascading_app(
                MessagingExtension()
                .bind(ShippedHandler, FailingAuditShippedHandler)
                .bind(PlaceOrderHandler)
                .bind(AuditSubscriber),
            ) as app,
            app.container() as container,
        ):
            bus = await container.get(IMessageBus)
            with pytest.raises(RuntimeError, match='sibling boom'):
                await bus.invoke(_OrderShipped(order='o-8'))

        # The sibling failure rolled back the whole fan-out frame; the staged non-durable cascade
        # was discarded with it (AuditSubscriber would pytest.fail if it ever ran).
        assert seen['uow'].rollback_count == 1
        assert seen['uow'].commit_count == 0
