"""Example demonstrating how to use Pipeline Behaviors with the Messaging extension.

This example shows:
1. How to create and register pipeline behaviors
2. How behaviors can implement cross-cutting concerns
3. Order of execution in the pipeline
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from waku import WakuFactory, module
from waku.messaging import (
    CallNext,
    IMessageBus,
    IPipelineBehavior,
    IRequest,
    MessageT,
    MessagingConfig,
    MessagingExtension,
    MessagingModule,
    RequestHandler,
    ResponseT,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class LoggingBehavior(IPipelineBehavior[MessageT, ResponseT]):
    """A pipeline behavior that logs request/response details."""

    def __init__(self) -> None:
        self._logger = logging.getLogger('cqrs.logger')
        self._log_level = logging.INFO

    async def handle(self, message: MessageT, /, call_next: CallNext[ResponseT]) -> ResponseT:
        request_name = type(message).__name__

        self._logger.log(self._log_level, 'Processing request %s: %s', request_name, message)

        try:
            response = await call_next()
        except Exception as err:
            self._logger.error('Error processing request %s: %s', request_name, err)
            raise

        self._logger.log(self._log_level, 'Completed request %s with response: %s', request_name, response)
        return response


class ValidationBehavior(IPipelineBehavior[MessageT, ResponseT]):
    """A pipeline behavior that validates requests before processing."""

    class ValidationError(Exception):
        """Raised when a request fails validation."""

        def __init__(self, errors: dict[str, Any]) -> None:
            self.errors = errors

        def __str__(self) -> str:
            errors = '; '.join(f'{key}: {value}' for key, value in self.errors.items())
            return f'Validation failed: {errors}'

    async def handle(self, message: MessageT, /, call_next: CallNext[ResponseT]) -> ResponseT:
        errors = await self._validate(message)
        if errors:
            raise self.ValidationError(errors)
        return await call_next()

    async def _validate(self, message: MessageT) -> dict[str, Any]:  # noqa: ARG002
        return {}


@dataclass(frozen=True, kw_only=True)
class UserResponse:
    """Response containing user information."""

    id: str
    name: str
    email: str | None = None


@dataclass(frozen=True, kw_only=True)
class GetUserQuery(IRequest[UserResponse]):
    """Query to retrieve user information by ID."""

    user_id: str


class GetUserQueryHandler(RequestHandler[GetUserQuery, UserResponse]):
    """Handler for the GetUserQuery."""

    async def handle(self, request: GetUserQuery, /) -> UserResponse:
        if request.user_id == '1':
            return UserResponse(id='1', name='John Doe', email='john@example.com')
        return UserResponse(id=request.user_id, name=f'User {request.user_id}')


class UserQueryValidationBehavior(ValidationBehavior[GetUserQuery, UserResponse]):
    """Specific validation behavior for user queries."""

    async def _validate(self, message: GetUserQuery) -> dict[str, Any]:
        errors: dict[str, Any] = {}
        if not message.user_id:
            errors['user_id'] = 'User ID cannot be empty'
        elif not message.user_id.isalnum():
            errors['user_id'] = 'User ID must contain only letters and numbers'
        return errors


@module(
    extensions=[
        (
            MessagingExtension().bind_request(
                GetUserQuery,
                GetUserQueryHandler,
                behaviors=[UserQueryValidationBehavior],
            )
        ),
    ],
)
class UserModule:
    """Module for user-related functionality."""


@module(
    imports=[
        UserModule,
        MessagingModule.register(
            MessagingConfig(
                pipeline_behaviors=[LoggingBehavior],
            ),
        ),
    ],
)
class AppModule:
    """Main application module."""


async def main() -> None:
    """Main function to demonstrate pipeline behaviors."""
    app = WakuFactory(AppModule).create()

    async with app, app.container() as container:
        bus = await container.get(IMessageBus)

        result = await bus.invoke(GetUserQuery(user_id='1'))
        logger.info('Query result: %s', result)

        try:
            await bus.invoke(GetUserQuery(user_id=''))
        except ValidationBehavior.ValidationError as err:
            logger.error('Validation error: %s', err)


if __name__ == '__main__':
    import asyncio

    asyncio.run(main())
