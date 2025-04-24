import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, ParamSpec, TypeVar
from uuid import UUID, uuid4

from dishka import AsyncContainer, FromDishka
from dishka.integrations.base import wrap_injection

from waku import WakuApplication, WakuFactory
from waku.mediator import (
    IMediator,
    MediatorConfig,
    MediatorModule,
    Request,
    RequestHandler,
    Response,
)
from waku.mediator.contracts.event import Event
from waku.mediator.events.handler import EventHandler
from waku.mediator.middlewares import BaseMiddleware, HandleType, Middleware, MiddlewareContext
from waku.mediator.modules import MediatorExtension
from waku.modules import module

P = ParamSpec('P')
T = TypeVar('T')

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class CreateMeetingResult(Response):
    meeting_id: UUID


@dataclass(frozen=True, kw_only=True)
class CreateMeetingCommand(Request[CreateMeetingResult]):
    user_id: UUID


@dataclass(frozen=True, kw_only=True)
class MeetingCreatedEvent(Event):
    user_id: UUID
    meeting_id: UUID


class CreatingMeetingCommandHandler(RequestHandler[CreateMeetingCommand, CreateMeetingResult]):
    def __init__(self, mediator: IMediator) -> None:
        self._mediator = mediator

    async def handle(self, request: CreateMeetingCommand) -> CreateMeetingResult:
        meeting_id = uuid4()
        logger.info('new meeting created user_id=%s', request.user_id)
        await self._mediator.publish(MeetingCreatedEvent(user_id=request.user_id, meeting_id=meeting_id))
        return CreateMeetingResult(meeting_id=meeting_id)


class MeetingCreatedEventHandler(EventHandler[MeetingCreatedEvent]):
    async def handle(self, event: MeetingCreatedEvent) -> None:
        logger.info('meeting created event handled user_id=%s', event.user_id)


class LogMiddleware(BaseMiddleware):
    def __init__(self, ctx: MiddlewareContext, *, log_level: int = logging.INFO) -> None:
        self._ctx = ctx
        self._log_level = log_level

    async def __call__(self, request: Request[Any], handle: HandleType) -> Response | None:
        logger.log(self._log_level, 'request=%s', request)
        response = await handle(request)
        logger.log(self._log_level, 'response=%s', response)
        return response


@asynccontextmanager
async def lifespan(_: WakuApplication) -> AsyncIterator[None]:
    logger.info('Lifespan startup')
    yield
    logger.info('Lifespan shutdown')


@module(
    extensions=[
        (
            MediatorExtension()
            .bind_request(CreateMeetingCommand, CreatingMeetingCommandHandler)
            .bind_event(MeetingCreatedEvent, [MeetingCreatedEventHandler])
        ),
    ],
)
class SomeModule:
    pass


@module(
    imports=[
        SomeModule,
        MediatorModule.register(MediatorConfig(middlewares=[Middleware(LogMiddleware)])),
    ],
)
class AppModule:
    pass


# Simple inject decorator for example purposes
# In real world you should import `@inject` decorator for your framework from `dishka.integrations.<framework>`
def _inject(func: Callable[P, T]) -> Callable[P, T]:
    return wrap_injection(
        func=func,
        is_async=True,
        container_getter=lambda args, _: args[0],
    )


# Define entrypoints
# In real world this can be FastAPI routes, etc.
@_inject
async def handler(
    container: AsyncContainer,  # noqa: ARG001
    mediator: FromDishka[IMediator],
) -> None:
    command = CreateMeetingCommand(user_id=uuid4())
    await mediator.send(command)


# Run the application
# In real world this would be run by a 3rd party framework like FastAPI
async def main() -> None:
    app = WakuFactory(AppModule, lifespan=[lifespan]).create()

    async with app, app.container() as request_container:
        await handler(request_container)  # type: ignore[call-arg]


if __name__ == '__main__':
    asyncio.run(main())
