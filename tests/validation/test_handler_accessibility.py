from __future__ import annotations

from dataclasses import dataclass

from typing_extensions import override

from waku import WakuFactory, module
from waku.di import scoped
from waku.messaging import (
    EventHandler,
    IEvent,
    IRequest,
    MessagingConfig,
    MessagingExtension,
    MessagingModule,
    RequestHandler,
)
from waku.messaging.contracts.pipeline import IPipelineBehavior, NextHandlerType
from waku.validation import ValidationExtension
from waku.validation.rules import DependenciesAccessibleRule


class IRepository:
    pass


class ConcreteRepository(IRepository):
    pass


@dataclass(frozen=True, kw_only=True)
class SomeEvent(IEvent):
    data: str


class EventHandlerWithDep(EventHandler[SomeEvent]):
    def __init__(self, repo: IRepository) -> None:
        self._repo = repo

    @override
    async def handle(self, event: SomeEvent, /) -> None:
        pass  # pragma: no cover


async def test_event_handler_deps_validated_against_originating_module() -> None:
    @module(
        providers=[scoped(IRepository, ConcreteRepository)],
        exports=[IRepository],
        extensions=[MessagingExtension().bind_event(SomeEvent, [EventHandlerWithDep])],
    )
    class DomainModule:
        pass

    @module(
        imports=[
            MessagingModule.register(MessagingConfig()),
            DomainModule,
        ],
    )
    class AppModule:
        pass

    app = WakuFactory(
        AppModule,
        extensions=[ValidationExtension([DependenciesAccessibleRule()], strict=True)],
    ).create()

    await app.initialize()


@dataclass(frozen=True, kw_only=True)
class ProcessResult:
    status: str


@dataclass(frozen=True, kw_only=True)
class ProcessCommand(IRequest[ProcessResult]):
    data: str


class ProcessCommandHandler(RequestHandler[ProcessCommand, ProcessResult]):
    def __init__(self, repo: IRepository) -> None:
        self._repo = repo

    @override
    async def handle(self, request: ProcessCommand, /) -> ProcessResult:
        return ProcessResult(status='ok')  # pragma: no cover


class ValidationBehavior(IPipelineBehavior[ProcessCommand, ProcessResult]):
    def __init__(self, repo: IRepository) -> None:
        self._repo = repo

    @override
    async def handle(
        self,
        request: ProcessCommand,
        /,
        next_handler: NextHandlerType[ProcessCommand, ProcessResult],
    ) -> ProcessResult:
        return await next_handler(request)  # pragma: no cover


async def test_pipeline_behavior_deps_validated_against_originating_module() -> None:
    @module(
        providers=[scoped(IRepository, ConcreteRepository)],
        exports=[IRepository],
        extensions=[
            MessagingExtension().bind_request(
                ProcessCommand,
                ProcessCommandHandler,
                behaviors=[ValidationBehavior],
            ),
        ],
    )
    class DomainModule:
        pass

    @module(
        imports=[
            MessagingModule.register(MessagingConfig()),
            DomainModule,
        ],
    )
    class AppModule:
        pass

    app = WakuFactory(
        AppModule,
        extensions=[ValidationExtension([DependenciesAccessibleRule()], strict=True)],
    ).create()

    await app.initialize()
