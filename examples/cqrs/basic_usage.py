"""Example demonstrating basic CQRS usage with the Mediator extension in Waku.

This example shows:
1. How to define CQRS commands, events, and handlers
2. How to register handlers with the Mediator extension
3. How to compose modules and application
4. How to create and use the application to send a command
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, ParamSpec, TypeVar

from waku import WakuApplication, WakuFactory, module
from waku.cqrs import (
    Event,
    EventHandler,
    IMediator,
    MediatorExtension,
    MediatorModule,
    Request,
    RequestHandler,
    Response,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

P = ParamSpec('P')
T = TypeVar('T')

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class CreateMeetingResult(Response):
    """Result of creating a meeting."""

    meeting_id: uuid.UUID


@dataclass(frozen=True, kw_only=True)
class CreateMeetingCommand(Request[CreateMeetingResult]):
    """Command to create a new meeting."""

    user_id: uuid.UUID


@dataclass(frozen=True, kw_only=True)
class MeetingCreatedEvent(Event):
    """Event triggered when a meeting is created."""

    user_id: uuid.UUID
    meeting_id: uuid.UUID


class CreatingMeetingCommandHandler(RequestHandler[CreateMeetingCommand, CreateMeetingResult]):
    """Handles CreateMeetingCommand by creating a meeting and publishing an event."""

    def __init__(self, mediator: IMediator) -> None:
        self._mediator = mediator

    async def handle(self, request: CreateMeetingCommand) -> CreateMeetingResult:
        """Handle the creation of a meeting.

        Args:
            request: The command containing user_id.

        Returns:
            CreateMeetingResult: The result containing the new meeting_id.
        """
        meeting_id = uuid.uuid4()
        logger.info('new meeting created user_id=%s', request.user_id)
        await self._mediator.publish(MeetingCreatedEvent(user_id=request.user_id, meeting_id=meeting_id))
        return CreateMeetingResult(meeting_id=meeting_id)


class MeetingCreatedEventHandler(EventHandler[MeetingCreatedEvent]):
    """Handles MeetingCreatedEvent by logging the event."""

    async def handle(self, event: MeetingCreatedEvent) -> None:
        """Handle the meeting created event.

        Args:
            event: The event containing user_id and meeting_id.
        """
        logger.info('meeting created event handled user_id=%s', event.user_id)


@asynccontextmanager
async def lifespan(_: WakuApplication) -> AsyncIterator[None]:
    """Application lifespan context manager for startup and shutdown logging."""
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
    """Module registering meeting command and event handlers."""


@module(
    imports=[
        SomeModule,
        MediatorModule.register(),
    ],
)
class AppModule:
    """Root application module importing all submodules."""


async def main() -> None:
    """Main function to demonstrate CQRS usage with meeting creation."""
    app = WakuFactory(AppModule).create()

    async with app, app.container() as container:
        mediator = await container.get(IMediator)

        command = CreateMeetingCommand(user_id=uuid.uuid4())
        await mediator.send(command)


if __name__ == '__main__':
    asyncio.run(main())
