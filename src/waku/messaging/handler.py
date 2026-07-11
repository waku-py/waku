from __future__ import annotations

import abc
from typing import TYPE_CHECKING, Any, ClassVar, Generic

from waku._internal.sentinel import MISSING
from waku.messaging.contracts.message import MessageT, ResponseT
from waku.messaging.contracts.request import RequestT

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import timedelta

    from waku.messaging.contracts.pipeline import IPipelineBehavior
    from waku.messaging.errors.policy import ErrorPolicy

__all__ = [
    'EventHandler',
    'MessageHandler',
    'RequestHandler',
]


class MessageHandler(abc.ABC, Generic[MessageT, ResponseT]):
    error_policies: ClassVar[Sequence[ErrorPolicy]] = ()
    """OVERRIDE: shadow `endpoint_defaults.error_policies` per-exception. Inherits via MRO (declaring replaces wholesale; extend via `(*Parent.error_policies, ...)`)."""

    behaviors: ClassVar[Sequence[type[IPipelineBehavior[Any, Any]]]] = ()
    """COMPOSE: framework + user-global behaviors wrap (outer); these run at the HANDLER_LOCAL tier (inner). Inherits via MRO."""

    execution_timeout: ClassVar[timedelta | MISSING | None] = MISSING  # type: ignore[valid-type]  # mypy lacks PEP 661 sentinel support; pyrefly narrows
    """OVERRIDE per-handler deadline. MISSING inherits `endpoint_defaults.execution_timeout`; None opts out; a timedelta sets it."""

    @abc.abstractmethod
    async def handle(self, message: MessageT, /) -> ResponseT:
        raise NotImplementedError


class RequestHandler(MessageHandler[RequestT, ResponseT]):
    @abc.abstractmethod
    async def handle(self, request: RequestT, /) -> ResponseT:
        raise NotImplementedError


class EventHandler(MessageHandler[MessageT, None]):
    @abc.abstractmethod
    async def handle(self, message: MessageT, /) -> None:
        raise NotImplementedError
