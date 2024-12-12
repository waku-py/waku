import asyncio
from dataclasses import dataclass

from lattice.application import Lattice
from lattice.di import Injected, inject
from lattice.di.contrib.aioinject import AioinjectDependencyProvider
from lattice.ext.mediator.extensions import MediatorAppExtension, MediatorModuleExtension
from lattice.ext.mediator.handlers.handler import Request, RequestHandler, Response
from lattice.ext.mediator.handlers.map import RequestMap
from lattice.ext.mediator.mediator import Mediator
from lattice.modules import Module


@dataclass(frozen=True, kw_only=True)
class ExampleResponse(Response):
    result: bool


@dataclass(frozen=True, kw_only=True)
class ExampleRequest(Request[ExampleResponse]):
    result: bool


class ExampleHandler(RequestHandler[ExampleRequest, ExampleResponse]):
    async def handle(self, request: ExampleRequest) -> ExampleResponse:  # noqa: PLR6301
        return ExampleResponse(result=request.result)


# fmt: off
module = Module(
    name='module',
    extensions=[
        MediatorModuleExtension(
            RequestMap()
            .bind(ExampleRequest, ExampleHandler)
        ),
    ],
)
# fmt: on

application = Lattice(
    name='app',
    modules=[module],
    dependency_provider=AioinjectDependencyProvider(),
    extensions=[
        MediatorAppExtension(),
    ],
)


@inject
async def handler(mediator: Injected[Mediator]) -> ExampleResponse:
    return await mediator.send(ExampleRequest(result=True))


async def main() -> None:
    dp = application.dependency_provider
    async with application.lifespan(), dp.lifespan(), dp.context():
        await handler()  # type: ignore[call-arg]


if __name__ == '__main__':
    asyncio.run(main())
