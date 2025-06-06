from dataclasses import dataclass

from waku import WakuFactory, module
from waku.di import Scope, contextual, scoped


@dataclass
class RequestContext:
    """Represents external request data passed from a web framework."""

    user_id: str
    session_id: str
    request_path: str


class LoggingService:
    """Service that uses contextual data for request-specific logging."""

    def __init__(self, request_ctx: RequestContext) -> None:
        self.request_ctx = request_ctx

    def log_action(self, action: str) -> str:
        return f'User {self.request_ctx.user_id} performed {action} on {self.request_ctx.request_path}'


@module(
    providers=[
        contextual(provided_type=RequestContext, scope=Scope.REQUEST),
        scoped(LoggingService),
    ],
)
class AppModule:
    pass


async def main() -> None:
    # Simulate external request data from a web framework
    request_data = RequestContext(user_id='user123', session_id='session456', request_path='/api/users')

    application = WakuFactory(AppModule).create()
    async with (
        application,
        application.container(
            context={RequestContext: request_data},
        ) as request_container,
    ):
        # LoggingService receives the contextual RequestContext automatically
        logging_service = await request_container.get(LoggingService)
        message = logging_service.log_action('update profile')
        print(message)  # "User user123 performed update profile on /api/users"


if __name__ == '__main__':
    import asyncio

    asyncio.run(main())
