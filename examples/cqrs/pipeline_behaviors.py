"""Example demonstrating how to use Pipeline Behaviors with the Mediator extension.

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
from waku.cqrs import (
    IMediator,
    IPipelineBehavior,
    MediatorConfig,
    MediatorExtension,
    MediatorModule,
    NextHandlerType,
    Request,
    RequestHandler,
    Response,
)
from waku.cqrs.contracts.request import RequestT, ResponseT

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class LoggingBehavior(IPipelineBehavior[RequestT, ResponseT]):
    """A pipeline behavior that logs request/response details."""

    def __init__(self) -> None:
        self._logger = logging.getLogger('cqrs.logger')
        self._log_level = logging.INFO

    async def handle(self, request: RequestT, /, next_handler: NextHandlerType[RequestT, ResponseT]) -> ResponseT:
        """Log request, process it through the pipeline, and log the response.

        Args:
            request: The request object.
            next_handler: The next handler in the pipeline.

        Returns:
            ResponseT: The response from the next handler.
        """
        request_name = type(request).__name__

        self._logger.log(
            self._log_level,
            'Processing request %s: %s',
            request_name,
            request,
        )

        try:
            response = await next_handler(request)

            self._logger.log(
                self._log_level,
                'Completed request %s with response: %s',
                request_name,
                response,
            )
        except Exception:
            self._logger.exception('Error processing request %s', request_name)
            raise
        else:
            return response


class ValidationBehavior(IPipelineBehavior[RequestT, ResponseT]):
    """A pipeline behavior that validates requests before processing."""

    class ValidationError(Exception):
        """Raised when a request fails validation."""

        def __init__(self, errors: dict[str, Any]) -> None:
            self.errors = errors

        def __str__(self) -> str:
            errors = '; '.join(f'{key}: {value}' for key, value in self.errors.items())
            return f'Validation failed: {errors}'

    async def handle(self, request: RequestT, /, next_handler: NextHandlerType[RequestT, ResponseT]) -> ResponseT:
        """Validate the request before passing it to the next handler.

        Args:
            request: The request object.
            next_handler: The next handler in the pipeline.

        Returns:
            ResponseT: The response from the next handler.

        Raises:
            ValidationError: If validation fails.
        """
        errors = await self._validate(request)
        if errors:
            raise self.ValidationError(errors)

        return await next_handler(request)

    async def _validate(self, request: RequestT) -> dict[str, Any]:  # noqa: ARG002
        """Validate the request and return any validation errors.

        Args:
            request: The request to validate.

        Returns:
            dict[str, Any]: A dictionary of validation errors, or an empty dict if validation passed.
        """
        return {}  # Default implementation performs no validation


@dataclass(frozen=True, kw_only=True)
class UserResponse(Response):
    """Response containing user information."""

    id: str
    name: str
    email: str | None = None


@dataclass(frozen=True, kw_only=True)
class GetUserQuery(Request[UserResponse]):
    """Query to retrieve user information by ID."""

    user_id: str


# 1. Simple Handler Implementation
class GetUserQueryHandler(RequestHandler[GetUserQuery, UserResponse]):
    """Handler for the GetUserQuery."""

    async def handle(self, request: GetUserQuery, /) -> UserResponse:
        """Process the query and return a user response.

        Args:
            request: The query containing the user_id.

        Returns:
            UserResponse: The user information response.
        """
        if request.user_id == '1':
            return UserResponse(id='1', name='John Doe', email='john@example.com')
        return UserResponse(id=request.user_id, name=f'User {request.user_id}')


# 2. Type-specific Validation Behavior
class UserQueryValidationBehavior(ValidationBehavior[GetUserQuery, UserResponse]):
    """Specific validation behavior for user queries."""

    async def _validate(self, request: GetUserQuery) -> dict[str, Any]:
        """Validate the GetUserQuery request.

        Args:
            request: The GetUserQuery to validate.

        Returns:
            dict[str, Any]: A dictionary of validation errors, or an empty dict if validation passed.
        """
        errors: dict[str, Any] = {}

        if not request.user_id:
            errors['user_id'] = 'User ID cannot be empty'
        elif not request.user_id.isalnum():
            errors['user_id'] = 'User ID must contain only letters and numbers'

        return errors


# 3. Application Setup
@module(
    extensions=[
        (
            MediatorExtension().bind_request(
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
        MediatorModule.register(
            MediatorConfig(
                pipeline_behaviors=[LoggingBehavior],
            ),
        ),
    ],
)
class AppModule:
    """Main application module."""


# 4. Application Creation and Usage
async def main() -> None:
    """Main function to demonstrate pipeline behaviors."""
    app = WakuFactory(AppModule).create()

    async with app, app.container() as container:
        mediator = await container.get(IMediator)

        # Send a valid query
        result = await mediator.send(GetUserQuery(user_id='1'))
        logger.info('Query result: %s', result)

        # Send an invalid query - will raise ValidationError
        try:
            await mediator.send(GetUserQuery(user_id=''))
        except ValidationBehavior.ValidationError as err:
            logger.error('Validation error: %s', err)


if __name__ == '__main__':
    import asyncio

    asyncio.run(main())
