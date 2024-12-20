import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from waku import Application, Module
from waku.di import Injected, inject
from waku.di.contrib.aioinject import AioinjectDependencyProvider
from waku.ext import DEFAULT_EXTENSIONS
from waku.ext.mediator import (
    Mediator,
    MediatorAppExtension,
    MediatorModuleExtension,
    MiddlewareChain,
    Request,
    RequestHandler,
    RequestMap,
    Response,
)
from waku.ext.mediator.handlers.handler import RequestT, ResponseT
from waku.ext.mediator.middlewares import HandleType, Middleware
from waku.extensions import OnApplicationShutdown, OnApplicationStartup

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class ExampleResponse(Response):
    result: bool


@dataclass(frozen=True, kw_only=True)
class ExampleRequest(Request[ExampleResponse]):
    result: bool


class ExampleMiddleware(Middleware[RequestT, ResponseT]):
    async def __call__(self, request: RequestT, handle: HandleType[RequestT, ResponseT]) -> ResponseT:
        logger.info('Mediator middleware')
        return await handle(request)


class ExampleHandler(RequestHandler[ExampleRequest, ExampleResponse]):
    async def handle(self, request: ExampleRequest) -> ExampleResponse:  # noqa: PLR6301
        return ExampleResponse(result=request.result)


# fmt: off
module = Module(
    name='module',
    exports=[ExampleHandler],
    extensions=[
        MediatorModuleExtension(
            RequestMap()
            .bind(ExampleRequest, ExampleHandler)
        ),
    ],
)
# fmt: on


@asynccontextmanager
async def lifespan(_: Application) -> AsyncIterator[None]:  # noqa: RUF029
    logger.info('Lifespan startup')
    yield
    logger.info('Lifespan shutdown')


class OnStartup(OnApplicationStartup):
    async def on_app_startup(self, _: Application) -> None:  # noqa: PLR6301
        logger.info('Application startup')


class OnShutdown(OnApplicationShutdown):
    def __init__(self, num: int) -> None:
        self._num = num

    async def on_app_shutdown(self, _: Application) -> None:
        logger.info('Application shutdown %s', self._num)


application = Application(
    name='app',
    modules=[module],
    dependency_provider=AioinjectDependencyProvider(),
    extensions=[
        MediatorAppExtension(MiddlewareChain([ExampleMiddleware()])),
        OnStartup(),
        OnShutdown(1),
        OnShutdown(2),
        *DEFAULT_EXTENSIONS,
    ],
    lifespan=[lifespan],
)


@inject
async def handler(mediator: Injected[Mediator]) -> ExampleResponse:
    return await mediator.send(ExampleRequest(result=True))


async def main() -> None:
    dp = application.dependency_provider
    async with application, dp.context():
        await handler()  # type: ignore[call-arg]


if __name__ == '__main__':
    asyncio.run(main())
