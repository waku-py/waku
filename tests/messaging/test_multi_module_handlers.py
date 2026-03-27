from __future__ import annotations

from dataclasses import dataclass

import pytest
from typing_extensions import override

from waku import WakuFactory, module
from waku.messaging import (
    EventHandler,
    IEvent,
    IMessageBus,
    IRequest,
    MessagingConfig,
    MessagingExtension,
    MessagingModule,
    RequestHandler,
)
from waku.messaging.contracts.pipeline import CallNext, IPipelineBehavior
from waku.messaging.exceptions import ImproperlyConfiguredError
from waku.messaging.registry import MessageRegistry


@dataclass(frozen=True, kw_only=True)
class OrderPlaced(IEvent):
    order_id: str


class SendEmailHandler(EventHandler[OrderPlaced]):
    @override
    async def handle(self, event: OrderPlaced, /) -> None:  # pragma: no cover
        pass


class UpdateStatsHandler(EventHandler[OrderPlaced]):
    @override
    async def handle(self, event: OrderPlaced, /) -> None:  # pragma: no cover
        pass


class AuditLogHandler(EventHandler[OrderPlaced]):
    @override
    async def handle(self, event: OrderPlaced, /) -> None:  # pragma: no cover
        pass


async def test_multi_module_event_handlers_all_resolved() -> None:
    @module(
        extensions=[MessagingExtension().bind(OrderPlaced, SendEmailHandler)],
    )
    class NotificationModule:
        pass

    @module(
        extensions=[MessagingExtension().bind(OrderPlaced, UpdateStatsHandler)],
    )
    class AnalyticsModule:
        pass

    @module(
        extensions=[MessagingExtension().bind(OrderPlaced, AuditLogHandler)],
    )
    class AuditModule:
        pass

    @module(
        imports=[
            MessagingModule.register(MessagingConfig()),
            NotificationModule,
            AnalyticsModule,
            AuditModule,
        ],
    )
    class AppModule:
        pass

    app = WakuFactory(AppModule).create()

    async with app, app.container() as container:
        message_registry = await container.get(MessageRegistry)
        handler_types = message_registry.handler_map.get_handler_types(OrderPlaced)
        assert len(handler_types) == 3

        resolved = {type(await container.get(ht)) for ht in handler_types}
        assert resolved == {SendEmailHandler, UpdateStatsHandler, AuditLogHandler}


async def test_multi_module_event_handlers_publish() -> None:
    called: list[str] = []

    class TrackingEmailHandler(EventHandler[OrderPlaced]):
        @override
        async def handle(self, event: OrderPlaced, /) -> None:
            called.append('email')

    class TrackingStatsHandler(EventHandler[OrderPlaced]):
        @override
        async def handle(self, event: OrderPlaced, /) -> None:
            called.append('stats')

    @module(
        extensions=[MessagingExtension().bind(OrderPlaced, TrackingEmailHandler)],
    )
    class ModuleA:
        pass

    @module(
        extensions=[MessagingExtension().bind(OrderPlaced, TrackingStatsHandler)],
    )
    class ModuleB:
        pass

    @module(
        imports=[MessagingModule.register(), ModuleA, ModuleB],
    )
    class AppModule:
        pass

    app = WakuFactory(AppModule).create()

    async with app, app.container() as container:
        bus = await container.get(IMessageBus)
        await bus.publish(OrderPlaced(order_id='ORD-1'))

    assert sorted(called) == ['email', 'stats']


@dataclass(frozen=True, kw_only=True)
class ProcessOrderResult:
    status: str


@dataclass(frozen=True, kw_only=True)
class ProcessOrder(IRequest[ProcessOrderResult]):
    order_id: str


class ProcessOrderHandler(RequestHandler[ProcessOrder, ProcessOrderResult]):
    @override
    async def handle(self, request: ProcessOrder, /) -> ProcessOrderResult:
        return ProcessOrderResult(status='ok')


async def test_multi_module_pipeline_behaviors_all_resolved() -> None:
    called: list[str] = []

    class GlobalLoggingBehavior(IPipelineBehavior[ProcessOrder, ProcessOrderResult]):
        @override
        async def handle(
            self,
            message: ProcessOrder,
            /,
            call_next: CallNext[ProcessOrderResult],
        ) -> ProcessOrderResult:
            called.append('global_logging')
            return await call_next()

    class RequestValidationBehavior(IPipelineBehavior[ProcessOrder, ProcessOrderResult]):
        @override
        async def handle(
            self,
            message: ProcessOrder,
            /,
            call_next: CallNext[ProcessOrderResult],
        ) -> ProcessOrderResult:
            called.append('request_validation')
            return await call_next()

    @module(
        extensions=[
            MessagingExtension().bind(ProcessOrder, ProcessOrderHandler, behaviors=[RequestValidationBehavior]),
        ],
    )
    class HandlerModule:
        pass

    @module(
        imports=[
            MessagingModule.register(MessagingConfig(pipeline_behaviors=[GlobalLoggingBehavior])),
            HandlerModule,
        ],
    )
    class AppModule:
        pass

    app = WakuFactory(AppModule).create()

    async with app, app.container() as container:
        bus = await container.get(IMessageBus)
        result = await bus.invoke(ProcessOrder(order_id='ORD-1'))

        assert result == ProcessOrderResult(status='ok')
        assert sorted(called) == ['global_logging', 'request_validation']


async def test_module_with_empty_messaging_extension_starts_without_error() -> None:
    @module(
        extensions=[MessagingExtension()],
    )
    class EmptyModule:
        pass

    @module(
        extensions=[MessagingExtension().bind(OrderPlaced, SendEmailHandler)],
    )
    class HandlerModule:
        pass

    @module(
        imports=[
            MessagingModule.register(MessagingConfig()),
            EmptyModule,
            HandlerModule,
        ],
    )
    class AppModule:
        pass

    app = WakuFactory(AppModule).create()

    async with app, app.container() as container:
        message_registry = await container.get(MessageRegistry)
        handler_types = message_registry.handler_map.get_handler_types(OrderPlaced)
        assert len(handler_types) == 1
        assert handler_types[0] is SendEmailHandler


def test_duplicate_handler_across_modules_raises_improperly_configured() -> None:
    @module(
        extensions=[MessagingExtension().bind(OrderPlaced, SendEmailHandler)],
    )
    class ModuleA:
        pass

    @module(
        extensions=[MessagingExtension().bind(OrderPlaced, SendEmailHandler)],
    )
    class ModuleB:
        pass

    @module(
        imports=[
            MessagingModule.register(MessagingConfig()),
            ModuleA,
            ModuleB,
        ],
    )
    class AppModule:
        pass

    with pytest.raises(ImproperlyConfiguredError, match=r'SendEmailHandler.*ModuleB'):
        WakuFactory(AppModule).create()
