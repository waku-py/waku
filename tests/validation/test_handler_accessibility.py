from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from typing_extensions import override

from waku import WakuFactory, module
from waku.di import scoped
from waku.messages import IEvent
from waku.messaging import (
    EventHandler,
    IRequest,
    MessageHandler,
    MessagingConfig,
    MessagingExtension,
    MessagingModule,
    RequestHandler,
)
from waku.messaging.contracts.pipeline import CallNext, IPipelineBehavior
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


async def assert_handler_dependencies_accessible(handler: type[MessageHandler[Any, Any]]) -> None:
    @module(
        providers=[scoped(IRepository, ConcreteRepository)],
        exports=[IRepository],
        extensions=[MessagingExtension().bind(handler)],
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

    async with app:
        pass


async def test_event_handler_deps_validated_against_originating_module() -> None:
    await assert_handler_dependencies_accessible(EventHandlerWithDep)


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
        message: ProcessCommand,
        /,
        call_next: CallNext[ProcessResult],
    ) -> ProcessResult:
        return await call_next()  # pragma: no cover


async def test_pipeline_behavior_deps_validated_against_originating_module() -> None:
    class ValidatingHandler(ProcessCommandHandler):
        behaviors = (ValidationBehavior,)

    await assert_handler_dependencies_accessible(ValidatingHandler)
