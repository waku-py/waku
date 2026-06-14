from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

import anyio
import pytest
from typing_extensions import override

from waku.messaging import (
    EventHandler,
    IEvent,
    IMessageBus,
    IOutgoingMessages,
    IRequest,
    MessagingExtension,
    MessagingModule,
    RequestHandler,
)
from waku.messaging.context import get_message_context
from waku.testing import create_test_app

if TYPE_CHECKING:
    from uuid import UUID


@dataclass(frozen=True, kw_only=True)
class _OrderId:
    value: str


@dataclass(frozen=True, kw_only=True)
class _PlaceOrder(IRequest[_OrderId]):
    item: str


@dataclass(frozen=True, kw_only=True)
class _OrderPlaced(IEvent):
    item: str


@dataclass(frozen=True, kw_only=True)
class _AuditRecorded(IEvent):
    note: str


class TestCascadingThroughInvoke:
    @staticmethod
    async def test_publish_cascaded_event_dispatched_after_handler_returns() -> None:
        received: list[_OrderPlaced] = []
        done = asyncio.Event()

        class PlaceOrderHandler(RequestHandler[_PlaceOrder, _OrderId]):
            def __init__(self, outgoing: IOutgoingMessages) -> None:
                self._outgoing = outgoing

            @override
            async def handle(self, cmd: _PlaceOrder, /) -> _OrderId:
                self._outgoing.publish(_OrderPlaced(item=cmd.item))
                return _OrderId(value='o-1')

        class OrderPlacedHandler(EventHandler[_OrderPlaced]):
            @override
            async def handle(self, event: _OrderPlaced, /) -> None:
                received.append(event)
                done.set()

        async with (
            create_test_app(
                imports=[MessagingModule.register()],
                extensions=[
                    MessagingExtension().bind(_PlaceOrder, PlaceOrderHandler).bind(_OrderPlaced, OrderPlacedHandler),
                ],
            ) as app,
            app.container() as container,
        ):
            bus = await container.get(IMessageBus)
            order_id = await bus.invoke(_PlaceOrder(item='widget'))
            with anyio.fail_after(5):
                await done.wait()

            assert order_id == _OrderId(value='o-1')
            assert [e.item for e in received] == ['widget']

    @staticmethod
    async def test_cascaded_event_inherits_parent_correlation_id() -> None:
        parent_ids: dict[str, UUID] = {}
        cascaded_ids: dict[str, UUID] = {}
        done = asyncio.Event()

        class PlaceOrderHandler(RequestHandler[_PlaceOrder, _OrderId]):
            def __init__(self, outgoing: IOutgoingMessages) -> None:
                self._outgoing = outgoing

            @override
            async def handle(self, cmd: _PlaceOrder, /) -> _OrderId:
                ctx = get_message_context()
                parent_ids['correlation_id'] = ctx.correlation_id
                parent_ids['message_id'] = ctx.message_id
                self._outgoing.publish(_OrderPlaced(item=cmd.item))
                return _OrderId(value='o-2')

        class OrderPlacedHandler(EventHandler[_OrderPlaced]):
            @override
            async def handle(self, event: _OrderPlaced, /) -> None:
                ctx = get_message_context()
                cascaded_ids['correlation_id'] = ctx.correlation_id
                cascaded_ids['causation_id'] = ctx.causation_id
                cascaded_ids['message_id'] = ctx.message_id
                done.set()

        async with (
            create_test_app(
                imports=[MessagingModule.register()],
                extensions=[
                    MessagingExtension().bind(_PlaceOrder, PlaceOrderHandler).bind(_OrderPlaced, OrderPlacedHandler),
                ],
            ) as app,
            app.container() as container,
        ):
            bus = await container.get(IMessageBus)
            await bus.invoke(_PlaceOrder(item='widget'))
            with anyio.fail_after(5):
                await done.wait()

        assert cascaded_ids['correlation_id'] == parent_ids['correlation_id']
        assert cascaded_ids['causation_id'] == parent_ids['message_id']
        assert cascaded_ids['message_id'] != parent_ids['message_id']

    @staticmethod
    async def test_multiple_cascaded_events_dispatched_in_fifo_order() -> None:
        received: list[str] = []
        audit_done = asyncio.Event()

        class MultiCascadeHandler(RequestHandler[_PlaceOrder, _OrderId]):
            def __init__(self, outgoing: IOutgoingMessages) -> None:
                self._outgoing = outgoing

            @override
            async def handle(self, cmd: _PlaceOrder, /) -> _OrderId:
                self._outgoing.publish(_OrderPlaced(item=cmd.item))
                self._outgoing.publish(_AuditRecorded(note='after-order'))
                return _OrderId(value='o-3')

        class OrderPlacedHandler(EventHandler[_OrderPlaced]):
            @override
            async def handle(self, event: _OrderPlaced, /) -> None:
                received.append(f'order:{event.item}')

        class AuditRecordedHandler(EventHandler[_AuditRecorded]):
            @override
            async def handle(self, event: _AuditRecorded, /) -> None:
                received.append(f'audit:{event.note}')
                audit_done.set()

        async with (
            create_test_app(
                imports=[MessagingModule.register()],
                extensions=[
                    MessagingExtension()
                    .bind(_PlaceOrder, MultiCascadeHandler)
                    .bind(_OrderPlaced, OrderPlacedHandler)
                    .bind(_AuditRecorded, AuditRecordedHandler),
                ],
            ) as app,
            app.container() as container,
        ):
            bus = await container.get(IMessageBus)
            await bus.invoke(_PlaceOrder(item='widget'))
            with anyio.fail_after(5):
                await audit_done.wait()

            assert received == ['order:widget', 'audit:after-order']


class TestCascadingErrorIsolation:
    @staticmethod
    async def test_outer_handler_failure_discards_cascaded_messages() -> None:
        received: list[_OrderPlaced] = []

        class FailingHandler(RequestHandler[_PlaceOrder, _OrderId]):
            def __init__(self, outgoing: IOutgoingMessages) -> None:
                self._outgoing = outgoing

            @override
            async def handle(self, cmd: _PlaceOrder, /) -> _OrderId:
                self._outgoing.publish(_OrderPlaced(item=cmd.item))
                msg = 'boom'
                raise RuntimeError(msg)

        class OrderPlacedHandler(EventHandler[_OrderPlaced]):
            @override
            async def handle(self, event: _OrderPlaced, /) -> None:  # pragma: no cover
                received.append(event)

        async with (
            create_test_app(
                imports=[MessagingModule.register()],
                extensions=[
                    MessagingExtension().bind(_PlaceOrder, FailingHandler).bind(_OrderPlaced, OrderPlacedHandler),
                ],
            ) as app,
            app.container() as container,
        ):
            bus = await container.get(IMessageBus)
            with pytest.raises(RuntimeError, match='boom'):
                await bus.invoke(_PlaceOrder(item='widget'))

        assert received == []

    @staticmethod
    async def test_cascaded_handler_failure_is_isolated_from_originator() -> None:
        cascade_ran = asyncio.Event()

        class Handler(RequestHandler[_PlaceOrder, _OrderId]):
            def __init__(self, outgoing: IOutgoingMessages) -> None:
                self._outgoing = outgoing

            @override
            async def handle(self, cmd: _PlaceOrder, /) -> _OrderId:
                self._outgoing.publish(_OrderPlaced(item=cmd.item))
                return _OrderId(value='o-err')

        class BrokenCascadeHandler(EventHandler[_OrderPlaced]):
            @override
            async def handle(self, event: _OrderPlaced, /) -> None:
                cascade_ran.set()
                msg = 'cascade boom'
                raise RuntimeError(msg)

        async with (
            create_test_app(
                imports=[MessagingModule.register()],
                extensions=[
                    MessagingExtension().bind(_PlaceOrder, Handler).bind(_OrderPlaced, BrokenCascadeHandler),
                ],
            ) as app,
            app.container() as container,
        ):
            bus = await container.get(IMessageBus)
            order_id = await bus.invoke(_PlaceOrder(item='widget'))
            with anyio.fail_after(5):
                await cascade_ran.wait()

            # Originator returned normally even though its cascade ran and raised.
            assert order_id == _OrderId(value='o-err')

    @staticmethod
    async def test_cascaded_send_with_no_route_is_logged_not_propagated(
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        @dataclass(frozen=True, kw_only=True)
        class _UnroutedCommand(IRequest[None]):
            pass

        class Handler(RequestHandler[_PlaceOrder, _OrderId]):
            def __init__(self, outgoing: IOutgoingMessages) -> None:
                self._outgoing = outgoing

            @override
            async def handle(self, cmd: _PlaceOrder, /) -> _OrderId:
                self._outgoing.send(_UnroutedCommand())
                return _OrderId(value='o-unrouted')

        async with (
            create_test_app(
                imports=[MessagingModule.register()],
                extensions=[MessagingExtension().bind(_PlaceOrder, Handler)],
            ) as app,
            app.container() as container,
        ):
            bus = await container.get(IMessageBus)
            with caplog.at_level(logging.ERROR, logger='waku.messaging.behaviors.cascading'):
                order_id = await bus.invoke(_PlaceOrder(item='widget'))

            assert order_id == _OrderId(value='o-unrouted')
            assert any('cascading message' in rec.message for rec in caplog.records)


class TestCascadingNested:
    @staticmethod
    async def test_nested_invoke_isolates_cascade_frames() -> None:
        received_outer: list[_OrderPlaced] = []
        received_inner: list[_AuditRecorded] = []
        outer_done = asyncio.Event()
        inner_done = asyncio.Event()

        @dataclass(frozen=True, kw_only=True)
        class _ValidateOrder(IRequest[None]):
            item: str

        class ValidateHandler(RequestHandler[_ValidateOrder, None]):
            def __init__(self, outgoing: IOutgoingMessages) -> None:
                self._outgoing = outgoing

            @override
            async def handle(self, cmd: _ValidateOrder, /) -> None:
                self._outgoing.publish(_AuditRecorded(note=f'validated:{cmd.item}'))

        class OuterHandler(RequestHandler[_PlaceOrder, _OrderId]):
            def __init__(self, outgoing: IOutgoingMessages, bus: IMessageBus) -> None:
                self._outgoing = outgoing
                self._bus = bus

            @override
            async def handle(self, cmd: _PlaceOrder, /) -> _OrderId:
                await self._bus.invoke(_ValidateOrder(item=cmd.item))
                self._outgoing.publish(_OrderPlaced(item=cmd.item))
                return _OrderId(value='o-nested')

        class OrderPlacedHandler(EventHandler[_OrderPlaced]):
            @override
            async def handle(self, event: _OrderPlaced, /) -> None:
                received_outer.append(event)
                outer_done.set()

        class AuditRecordedHandler(EventHandler[_AuditRecorded]):
            @override
            async def handle(self, event: _AuditRecorded, /) -> None:
                received_inner.append(event)
                inner_done.set()

        async with (
            create_test_app(
                imports=[MessagingModule.register()],
                extensions=[
                    MessagingExtension()
                    .bind(_PlaceOrder, OuterHandler)
                    .bind(_ValidateOrder, ValidateHandler)
                    .bind(_OrderPlaced, OrderPlacedHandler)
                    .bind(_AuditRecorded, AuditRecordedHandler),
                ],
            ) as app,
            app.container() as container,
        ):
            bus = await container.get(IMessageBus)
            await bus.invoke(_PlaceOrder(item='widget'))
            with anyio.fail_after(5):
                await outer_done.wait()
                await inner_done.wait()

            assert [e.item for e in received_outer] == ['widget']
            assert [e.note for e in received_inner] == ['validated:widget']

    @staticmethod
    async def test_inner_handler_failure_preserves_outer_cascade() -> None:
        received: list[_OrderPlaced] = []
        audit_observed: list[_AuditRecorded] = []
        outer_done = asyncio.Event()

        @dataclass(frozen=True, kw_only=True)
        class _ValidateOrder(IRequest[None]):
            pass

        class FailingValidator(RequestHandler[_ValidateOrder, None]):
            def __init__(self, outgoing: IOutgoingMessages) -> None:
                self._outgoing = outgoing

            @override
            async def handle(self, cmd: _ValidateOrder, /) -> None:
                self._outgoing.publish(_AuditRecorded(note='should-be-discarded'))
                msg = 'validation failed'
                raise RuntimeError(msg)

        class ToleratingOuter(RequestHandler[_PlaceOrder, _OrderId]):
            def __init__(self, outgoing: IOutgoingMessages, bus: IMessageBus) -> None:
                self._outgoing = outgoing
                self._bus = bus

            @override
            async def handle(self, cmd: _PlaceOrder, /) -> _OrderId:
                with contextlib.suppress(RuntimeError):
                    await self._bus.invoke(_ValidateOrder())
                self._outgoing.publish(_OrderPlaced(item=cmd.item))
                return _OrderId(value='o-tolerated')

        class OrderPlacedHandler(EventHandler[_OrderPlaced]):
            @override
            async def handle(self, event: _OrderPlaced, /) -> None:
                received.append(event)
                outer_done.set()

        class AuditHandler(EventHandler[_AuditRecorded]):
            @override
            async def handle(self, event: _AuditRecorded, /) -> None:  # pragma: no cover
                audit_observed.append(event)

        async with (
            create_test_app(
                imports=[MessagingModule.register()],
                extensions=[
                    MessagingExtension()
                    .bind(_PlaceOrder, ToleratingOuter)
                    .bind(_ValidateOrder, FailingValidator)
                    .bind(_OrderPlaced, OrderPlacedHandler)
                    .bind(_AuditRecorded, AuditHandler),
                ],
            ) as app,
            app.container() as container,
        ):
            bus = await container.get(IMessageBus)
            await bus.invoke(_PlaceOrder(item='widget'))
            with anyio.fail_after(5):
                await outer_done.wait()

            assert [e.item for e in received] == ['widget']
            assert audit_observed == []


class TestCascadingViaPublishAndSend:
    @staticmethod
    async def test_cascade_through_publish_root_call() -> None:
        received: list[_AuditRecorded] = []
        audit_done = asyncio.Event()

        class OrderPlacedCascader(EventHandler[_OrderPlaced]):
            def __init__(self, outgoing: IOutgoingMessages) -> None:
                self._outgoing = outgoing

            @override
            async def handle(self, event: _OrderPlaced, /) -> None:
                self._outgoing.publish(_AuditRecorded(note=f'seen:{event.item}'))

        class AuditHandler(EventHandler[_AuditRecorded]):
            @override
            async def handle(self, event: _AuditRecorded, /) -> None:
                received.append(event)
                audit_done.set()

        async with (
            create_test_app(
                imports=[MessagingModule.register()],
                extensions=[
                    MessagingExtension().bind(_OrderPlaced, OrderPlacedCascader).bind(_AuditRecorded, AuditHandler),
                ],
            ) as app,
            app.container() as container,
        ):
            bus = await container.get(IMessageBus)
            await bus.publish(_OrderPlaced(item='widget'))
            with anyio.fail_after(5):
                await audit_done.wait()

            assert [e.note for e in received] == ['seen:widget']

    @staticmethod
    async def test_cascaded_send_routes_to_handler() -> None:
        fulfilled: list[str] = []
        done = asyncio.Event()

        @dataclass(frozen=True, kw_only=True)
        class _FulfillOrder(IRequest[None]):
            item: str

        class PlaceOrderHandler(RequestHandler[_PlaceOrder, _OrderId]):
            def __init__(self, outgoing: IOutgoingMessages) -> None:
                self._outgoing = outgoing

            @override
            async def handle(self, cmd: _PlaceOrder, /) -> _OrderId:
                self._outgoing.send(_FulfillOrder(item=cmd.item))
                return _OrderId(value='o-send')

        class FulfillHandler(RequestHandler[_FulfillOrder, None]):
            @override
            async def handle(self, cmd: _FulfillOrder, /) -> None:
                fulfilled.append(cmd.item)
                done.set()

        async with (
            create_test_app(
                imports=[MessagingModule.register()],
                extensions=[
                    MessagingExtension().bind(_PlaceOrder, PlaceOrderHandler).bind(_FulfillOrder, FulfillHandler),
                ],
            ) as app,
            app.container() as container,
        ):
            bus = await container.get(IMessageBus)
            order_id = await bus.invoke(_PlaceOrder(item='widget'))
            with anyio.fail_after(5):
                await done.wait()

            assert order_id == _OrderId(value='o-send')
            assert fulfilled == ['widget']
