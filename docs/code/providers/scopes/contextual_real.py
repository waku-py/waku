from fastapi import FastAPI, Request
from waku import WakuFactory, module
from waku.di import contextual, scoped, Scope


class UserService:
    """Service that uses the current HTTP request for user-specific operations."""

    def __init__(self, request: Request) -> None:
        self.request = request

    def get_user_info(self) -> dict[str, str]:
        """Extract user information from the request headers."""
        return {
            'user_id': self.request.headers.get('user-id', 'anonymous'),
            'session_id': self.request.headers.get('session-id', 'none'),
            'user_agent': self.request.headers.get('user-agent', 'unknown'),
        }


@module(
    providers=[
        contextual(provided_type=Request, scope=Scope.REQUEST),
        scoped(UserService),
    ],
)
class WebModule:
    pass


# FastAPI application setup
app = FastAPI()
application = WakuFactory(WebModule).create()


@app.get('/user-info')
async def get_user_info(request: Request) -> dict[str, str]:
    """Endpoint that uses contextual dependency injection."""
    async with (
        application,
        application.container(
            context={Request: request},
        ) as request_container,
    ):
        # UserService automatically receives the current HTTP request
        user_service = await request_container.get(UserService)
        return user_service.get_user_info()


# Example usage:
# curl -H "user-id: john123" -H "session-id: abc456" http://localhost:8000/user-info
