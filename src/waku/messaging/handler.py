from __future__ import annotations

import abc
from typing import Generic

from waku.messaging.contracts.message import MessageT, ResponseT
from waku.messaging.contracts.request import RequestT

__all__ = [
    'EventHandler',
    'MessageHandler',
    'RequestHandler',
]


class MessageHandler(abc.ABC, Generic[MessageT, ResponseT]):
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
