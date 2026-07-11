from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest
from typing_extensions import override

from waku.di import object_
from waku.messages import IEvent
from waku.messaging import (
    EventHandler,
    IMessageBus,
    IRequest,
    MessagingConfig,
    MessagingExtension,
    MessagingModule,
    RequestHandler,
)
from waku.messaging.behaviors.transactional import TransactionalBehavior
from waku.messaging.context import get_message_context
from waku.messaging.exceptions import HandlerNotFound
from waku.testing import create_test_app
from waku.uow import IUnitOfWork

from tests.messaging.helpers import FakeUoW

if TYPE_CHECKING:
    from contextlib import AbstractAsyncContextManager

    from waku.application import WakuApplication


@dataclass(frozen=True, kw_only=True)
class _OrderShipped(IEvent):
    order: str


@dataclass(frozen=True, kw_only=True)
class _PlaceOrder(IRequest[None]):
    order: str


def _transactional_app(uow: FakeUoW, extension: MessagingExtension) -> AbstractAsyncContextManager[WakuApplication]:
    return create_test_app(
        providers=[object_(uow, provided_type=IUnitOfWork)],
        imports=[MessagingModule.register(MessagingConfig(global_pipeline_behaviors=[TransactionalBehavior]))],
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
            with pytest.raises(HandlerNotFound, match='_OrderShipped'):
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
        uow = FakeUoW()
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
        uow = FakeUoW()

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
        uow = FakeUoW()

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
