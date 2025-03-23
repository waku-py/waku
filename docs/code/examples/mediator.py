import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from uuid import UUID, uuid4

from waku import Application, ApplicationFactory
from waku.di import Injected, inject
from waku.di.contrib.aioinject import AioinjectDependencyProvider
from waku.mediator import (
    IMediator,
    MediatorConfig,
    MediatorModule,
    Request,
    RequestHandler,
    Response,
)
from waku.mediator.contracts.event import Event
from waku.mediator.contracts.request import RequestT, ResponseT
from waku.mediator.events.handler import EventHandler
from waku.mediator.middlewares import HandleType, Middleware
from waku.mediator.modules import MediatorExtension
from waku.modules import module

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
    async def handle(self, event: MeetingCreatedEvent) -> None:  # noqa: PLR6301
        logger.info('meeting created event handled user_id=%s', event.user_id)


class LogMiddleware(Middleware[RequestT, ResponseT]):
    async def __call__(self, request: RequestT, handle: HandleType[RequestT, ResponseT]) -> ResponseT:
        logger.info('request=%s', request)
        response = await handle(request)
        logger.info('response=%s', response)
        return response


@asynccontextmanager
async def lifespan(_: Application) -> AsyncIterator[None]:
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
        MediatorModule.register(MediatorConfig(middlewares=[LogMiddleware])),
    ],
)
class AppModule:
    pass


@inject
async def handler(mediator: Injected[IMediator]) -> None:
    command = CreateMeetingCommand(user_id=uuid4())
    await mediator.send(command)


async def main() -> None:
    dp = AioinjectDependencyProvider()
    app = ApplicationFactory.create(
        AppModule,
        dependency_provider=dp,
        lifespan=[lifespan],
    )

    async with app, app.container.context():
        await handler()  # type: ignore[call-arg]


if __name__ == '__main__':
    asyncio.run(main())
