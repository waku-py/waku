"""Example demonstrating improved provider registration patterns in Waku.

This example shows various approaches to provider registration:
1. Simple registration with automatic lifetime inference
2. Interface-to-implementation binding
3. Factory providers for complex object creation
4. Contextual providers for request-specific data
"""

from __future__ import annotations

import asyncio
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Protocol

from waku import WakuFactory, module
from waku.di import Scope, contextual, scoped


@dataclass
class DatabaseConfig:
    """Database configuration."""

    connection_string: str


@dataclass
class SmtpConfig:
    """SMTP configuration."""

    host: str
    port: int
    username: str


# Domain interfaces
class IUserRepository(Protocol):
    """Repository interface for user data access."""

    async def get_by_id(self, user_id: str) -> dict[str, object] | None:
        """Get user by ID."""


class ILogger(ABC):
    """Logger interface."""

    @abstractmethod
    def log(self, message: str) -> None:
        """Log a message."""


class INotificationService(Protocol):
    """Notification service interface."""

    async def send(self, message: str, recipient: str) -> bool:
        """Send a notification."""


# Implementations
class SqlUserRepository:
    """SQL implementation of user repository."""

    def __init__(self, config: DatabaseConfig) -> None:
        self.config = config

    async def get_by_id(self, user_id: str) -> dict[str, object] | None:
        """Get user by ID from SQL database."""
        return {
            'id': user_id,
            'name': f'User {user_id}',
            'source': 'sql',
            'db': self.config.connection_string,
        }


class FileLogger(ILogger):
    """File-based logger implementation."""

    def __init__(self, file_path: str) -> None:
        self.file_path = file_path

    def log(self, message: str) -> None:
        """Log message to file."""
        print(f'[FILE:{self.file_path}] {message}')


class ConsoleLogger(ILogger):
    """Console-based logger implementation."""

    def log(self, message: str) -> None:
        """Log message to console."""
        print(f'[CONSOLE] {message}')


class EmailService:
    """Email notification service."""

    def __init__(self, config: SmtpConfig) -> None:
        self.config = config

    async def send(self, message: str, recipient: str) -> bool:
        """Send email notification."""
        print(f'EMAIL to {recipient}: {message} (config: {self.config})')
        return True


class UserService:
    """Business logic service for users."""

    def __init__(
        self,
        repository: IUserRepository,
        logger: ILogger,
        notification_service: INotificationService,
    ) -> None:
        self.repository = repository
        self.logger = logger
        self.notification_service = notification_service

    async def get_user(self, user_id: str) -> dict[str, object] | None:
        """Get user and log the action."""
        self.logger.log(f'Fetching user {user_id}')
        user = await self.repository.get_by_id(user_id)

        if user:
            await self.notification_service.send(f'User {user_id} accessed', 'admin@example.com')

        return user


@dataclass
class RequestContext:
    """Request context with environment information."""

    environment: str
    request_id: str


# Factory functions for configuration
def create_database_config() -> DatabaseConfig:
    """Create database configuration from environment."""
    return DatabaseConfig(connection_string=os.getenv('DATABASE_URL', 'sqlite:///app.db'))


async def create_smtp_config() -> SmtpConfig:
    """Create SMTP configuration asynchronously."""
    return SmtpConfig(
        host=os.getenv('SMTP_HOST', 'localhost'),
        port=int(os.getenv('SMTP_PORT', '587')),
        username=os.getenv('SMTP_USER', 'user'),
    )


def create_logger_for_env(context: RequestContext) -> ILogger:
    """Create logger based on environment."""
    if context.environment == 'dev':
        return ConsoleLogger()
    return FileLogger(f'/var/log/app_{context.environment}.log')


# Example 1: Simple registration with improved interface binding
@module(
    providers=[
        # Configuration as regular classes
        scoped(DatabaseConfig, create_database_config),
        scoped(SmtpConfig, create_smtp_config),
        # Interface-to-implementation binding
        scoped(IUserRepository, SqlUserRepository),
        scoped(INotificationService, EmailService),
        scoped(UserService),  # Simple registration, auto-scoped
        # Contextual provider
        contextual(provided_type=RequestContext, scope=Scope.REQUEST),
    ],
)
class SimpleModule:
    """Module using simple registration patterns."""


# Example 2: Factory providers and contextual dependencies
@module(
    providers=[
        # Factory providers for configuration
        scoped(DatabaseConfig, create_database_config),
        scoped(SmtpConfig, create_smtp_config),
        scoped(ILogger, create_logger_for_env),
        # Interface-to-implementation binding
        scoped(IUserRepository, SqlUserRepository),
        scoped(INotificationService, EmailService),
        # Simple service registration
        scoped(UserService),
        # Context provider
        contextual(RequestContext, scope=Scope.REQUEST),
    ],
)
class AdvancedModule:
    """Module using factory providers and contextual dependencies."""


# Main application module
@module(imports=[AdvancedModule])
class AppModule:
    """Root application module."""


async def main() -> None:
    """Demonstrate the improved provider registration."""
    # Simulate request context
    request_context = RequestContext(environment='dev', request_id='req-123')

    app = WakuFactory(AppModule).create()

    async with app, app.container(context={RequestContext: request_context}) as container:
        # All dependencies are automatically injected
        user_service = await container.get(UserService)

        # Test the service
        user = await user_service.get_user('user-456')
        print(f'Retrieved user: {user}')

        # Test configuration access
        db_config = await container.get(DatabaseConfig)
        smtp_config = await container.get(SmtpConfig)

        print(f'Database config: {db_config}')
        print(f'SMTP config: {smtp_config}')


if __name__ == '__main__':
    asyncio.run(main())
