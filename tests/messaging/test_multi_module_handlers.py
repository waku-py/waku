from __future__ import annotations

from dataclasses import dataclass

import pytest
from typing_extensions import override

from waku import WakuFactory, module
from waku.exceptions import ImproperlyConfiguredError
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
from waku.messaging._internal.registry import MessageRegistry
from waku.messaging.contracts.pipeline import CallNext, IPipelineBehavior


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
        extensions=[MessagingExtension().bind(SendEmailHandler)],
    )
    class NotificationModule:
        pass

    @module(
        extensions=[MessagingExtension().bind(UpdateStatsHandler)],
    )
    class AnalyticsModule:
        pass

    @module(
        extensions=[MessagingExtension().bind(AuditLogHandler)],
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
        extensions=[MessagingExtension().bind(TrackingEmailHandler)],
    )
    class ModuleA:
        pass

    @module(
        extensions=[MessagingExtension().bind(TrackingStatsHandler)],
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

    class ValidatingHandler(ProcessOrderHandler):
        behaviors = (RequestValidationBehavior,)

    @module(
        extensions=[
            MessagingExtension().bind(ValidatingHandler),
        ],
    )
    class HandlerModule:
        pass

    @module(
        imports=[
            MessagingModule.register(MessagingConfig(global_pipeline_behaviors=[GlobalLoggingBehavior])),
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
        extensions=[MessagingExtension().bind(SendEmailHandler)],
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
        extensions=[MessagingExtension().bind(SendEmailHandler)],
    )
    class ModuleA:
        pass

    @module(
        extensions=[MessagingExtension().bind(SendEmailHandler)],
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


async def test_behavior_shared_across_modules_registers_once() -> None:
    seen: list[str] = []

    class SharedBehavior(IPipelineBehavior[IEvent, None]):
        @override
        async def handle(self, message: IEvent, /, call_next: CallNext[None]) -> None:
            seen.append(type(message).__name__)
            return await call_next()

    @dataclass(frozen=True)
    class EventOne(IEvent):
        pass

    @dataclass(frozen=True)
    class EventTwo(IEvent):
        pass

    class HandlerOne(EventHandler[EventOne]):
        behaviors = (SharedBehavior,)

        @override
        async def handle(self, event: EventOne, /) -> None: ...

    class HandlerTwo(EventHandler[EventTwo]):
        behaviors = (SharedBehavior,)

        @override
        async def handle(self, event: EventTwo, /) -> None: ...

    @module(extensions=[MessagingExtension().bind(HandlerOne)])
    class ModuleOne:
        pass

    @module(extensions=[MessagingExtension().bind(HandlerTwo)])
    class ModuleTwo:
        pass

    @module(imports=[MessagingModule.register(MessagingConfig()), ModuleOne, ModuleTwo])
    class AppModule:
        pass

    # A duplicate scoped provider for SharedBehavior would raise at container build.
    async with WakuFactory(AppModule).create() as app, app.container() as container:
        bus = await container.get(IMessageBus)
        await bus.publish(EventOne())
        await bus.publish(EventTwo())

    assert sorted(seen) == ['EventOne', 'EventTwo']


async def test_handler_bound_to_multiple_message_types_registers_once() -> None:
    seen: list[str] = []

    @dataclass(frozen=True)
    class EventX(IEvent):
        pass

    @dataclass(frozen=True)
    class EventY(IEvent):
        pass

    class MultiHandler(EventHandler[IEvent]):
        @override
        async def handle(self, event: IEvent, /) -> None:
            seen.append(type(event).__name__)

    @module(extensions=[MessagingExtension().bind(EventX, MultiHandler).bind(EventY, MultiHandler)])
    class HandlerModule:
        pass

    @module(imports=[MessagingModule.register(MessagingConfig()), HandlerModule])
    class AppModule:
        pass

    # The handler is bound to two message types; it must register only once.
    async with WakuFactory(AppModule).create() as app, app.container() as container:
        bus = await container.get(IMessageBus)
        await bus.publish(EventX())
        await bus.publish(EventY())

    assert sorted(seen) == ['EventX', 'EventY']
