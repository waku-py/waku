from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from typing_extensions import override

from waku import WakuFactory, module
from waku.cqrs import (
    Event,
    EventHandler,
    IMediator,
    MediatorConfig,
    MediatorExtension,
    MediatorModule,
    Request,
    RequestHandler,
    Response,
)
from waku.cqrs.contracts.pipeline import IPipelineBehavior, NextHandlerType
from waku.cqrs.registry import MediatorRegistry


@dataclass(frozen=True, kw_only=True)
class OrderPlaced(Event):
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
        extensions=[MediatorExtension().bind_event(OrderPlaced, [SendEmailHandler])],
    )
    class NotificationModule:
        pass

    @module(
        extensions=[MediatorExtension().bind_event(OrderPlaced, [UpdateStatsHandler])],
    )
    class AnalyticsModule:
        pass

    @module(
        extensions=[MediatorExtension().bind_event(OrderPlaced, [AuditLogHandler])],
    )
    class AuditModule:
        pass

    @module(
        imports=[
            MediatorModule.register(MediatorConfig()),
            NotificationModule,
            AnalyticsModule,
            AuditModule,
        ],
    )
    class AppModule:
        pass

    app = WakuFactory(AppModule).create()

    async with app, app.container() as container:
        mediator_registry = await container.get(MediatorRegistry)
        assert len(mediator_registry.event_map.registry[OrderPlaced].handler_types) == 3

        handler_type = mediator_registry.event_map.get_handler_type(OrderPlaced)
        handlers = await container.get(Sequence[handler_type])  # type: ignore[valid-type]
        assert len(handlers) == 3

        handler_classes = {type(h) for h in handlers}
        assert handler_classes == {SendEmailHandler, UpdateStatsHandler, AuditLogHandler}


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
        extensions=[MediatorExtension().bind_event(OrderPlaced, [TrackingEmailHandler])],
    )
    class ModuleA:
        pass

    @module(
        extensions=[MediatorExtension().bind_event(OrderPlaced, [TrackingStatsHandler])],
    )
    class ModuleB:
        pass

    @module(
        imports=[MediatorModule.register(), ModuleA, ModuleB],
    )
    class AppModule:
        pass

    app = WakuFactory(AppModule).create()

    async with app, app.container() as container:
        mediator = await container.get(IMediator)
        await mediator.publish(OrderPlaced(order_id='ORD-1'))

    assert sorted(called) == ['email', 'stats']


@dataclass(frozen=True, kw_only=True)
class ProcessOrderResult(Response):
    status: str


@dataclass(frozen=True, kw_only=True)
class ProcessOrder(Request[ProcessOrderResult]):
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
            request: ProcessOrder,
            /,
            next_handler: NextHandlerType[ProcessOrder, ProcessOrderResult],
        ) -> ProcessOrderResult:
            called.append('global_logging')
            return await next_handler(request)

    class RequestValidationBehavior(IPipelineBehavior[ProcessOrder, ProcessOrderResult]):
        @override
        async def handle(
            self,
            request: ProcessOrder,
            /,
            next_handler: NextHandlerType[ProcessOrder, ProcessOrderResult],
        ) -> ProcessOrderResult:
            called.append('request_validation')
            return await next_handler(request)

    @module(
        extensions=[
            MediatorExtension().bind_request(ProcessOrder, ProcessOrderHandler, behaviors=[RequestValidationBehavior]),
        ],
    )
    class HandlerModule:
        pass

    @module(
        imports=[
            MediatorModule.register(MediatorConfig(pipeline_behaviors=[GlobalLoggingBehavior])),
            HandlerModule,
        ],
    )
    class AppModule:
        pass

    app = WakuFactory(AppModule).create()

    async with app, app.container() as container:
        mediator = await container.get(IMediator)
        result = await mediator.send(ProcessOrder(order_id='ORD-1'))

        assert result == ProcessOrderResult(status='ok')
        assert sorted(called) == ['global_logging', 'request_validation']
