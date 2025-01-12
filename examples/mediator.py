import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from uuid import UUID, uuid4

from waku import Application, Module
from waku.application import ApplicationConfig
from waku.di import Injected, inject
from waku.di.contrib.aioinject import AioinjectDependencyProvider
from waku.ext import DEFAULT_EXTENSIONS
from waku.ext.mediator import (
    MediatorAppExtension,
    MediatorModuleExtension,
    Request,
    RequestHandler,
    RequestMap,
    Response,
)
from waku.ext.mediator.contracts.event import Event
from waku.ext.mediator.contracts.request import RequestT, ResponseT
from waku.ext.mediator.events.handler import EventHandler
from waku.ext.mediator.events.map import EventMap
from waku.ext.mediator.extensions import MediatorExtensionConfig
from waku.ext.mediator.mediator import IMediator
from waku.ext.mediator.middlewares import HandleType, Middleware
from waku.extensions import ApplicationLifespan

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
        await self._mediator.publish(MeetingCreatedEvent(user_id=request.user_id, meeting_id=meeting_id))
        return CreateMeetingResult(meeting_id=meeting_id)


class MeetingCreatedEventHandler(EventHandler[MeetingCreatedEvent]):
    async def handle(self, event: MeetingCreatedEvent) -> None:  # noqa: PLR6301
        logger.info('new meeting created by user_id=%s', event.user_id)


class LogMiddleware(Middleware[RequestT, ResponseT]):
    async def __call__(self, request: RequestT, handle: HandleType[RequestT, ResponseT]) -> ResponseT:
        logger.info('request=%s', request)
        response = await handle(request)
        logger.info('response=%s', response)
        return response


module = Module(
    name='module',
    exports=[CreatingMeetingCommandHandler],
    extensions=[
        MediatorModuleExtension(
            RequestMap().bind(CreateMeetingCommand, CreatingMeetingCommandHandler),
            EventMap().bind(MeetingCreatedEvent, [MeetingCreatedEventHandler]),
        ),
    ],
)


@asynccontextmanager
async def lifespan(_: Application) -> AsyncIterator[None]:  # noqa: RUF029
    logger.info('Lifespan startup')
    yield
    logger.info('Lifespan shutdown')


class Lifespan(ApplicationLifespan):
    def __init__(self, num: int) -> None:
        self._num = num

    @contextlib.asynccontextmanager
    async def lifespan(self, _: Application) -> AsyncIterator[None]:
        logger.info('Application startup')
        try:
            yield
        finally:
            logger.info('Application shutdown %s', self._num)


application = Application(
    name='app',
    config=ApplicationConfig(
        modules=[module],
        dependency_provider=AioinjectDependencyProvider(),
        extensions=[
            MediatorAppExtension(MediatorExtensionConfig(middlewares=[LogMiddleware])),
            Lifespan(1),
            Lifespan(2),
            *DEFAULT_EXTENSIONS,
        ],
        lifespan=[lifespan],
    ),
)


@inject
async def handler(mediator: Injected[IMediator]) -> CreateMeetingResult:
    return await mediator.send(CreateMeetingCommand(user_id=uuid4()))


async def main() -> None:
    dp = application.dependency_provider
    async with application, dp.context():
        result = await handler()  # type: ignore[call-arg]

    print(result)  # noqa: T201


if __name__ == '__main__':
    asyncio.run(main())
