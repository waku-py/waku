from __future__ import annotations

from typing import TYPE_CHECKING

from waku.exceptions import ImproperlyConfiguredError, WakuError

if TYPE_CHECKING:
    from waku.messaging.contracts.handler import HandlerType
    from waku.messaging.contracts.message import IMessage

__all__ = [
    'HandlerAlreadyRegistered',
    'HandlerNotFound',
    'ImproperlyConfiguredError',
    'MapFrozenError',
    'MessagingError',
    'MultipleHandlersRegistered',
    'NoRouteError',
]


class MessagingError(WakuError):
    pass


class MapFrozenError(MessagingError):
    def __init__(self) -> None:
        super().__init__('Cannot modify map after it is frozen')


class HandlerNotFound(MessagingError):  # noqa: N818
    def __init__(self, message_type: type[IMessage]) -> None:
        self.message_type = message_type

    def __str__(self) -> str:
        return f'No handler registered for {self.message_type.__name__}'


class NoRouteError(MessagingError):
    def __init__(self, message_type: type[IMessage]) -> None:
        self.message_type = message_type

    def __str__(self) -> str:
        return f'No route found for {self.message_type.__name__}'


class HandlerAlreadyRegistered(ImproperlyConfiguredError):  # noqa: N818
    def __init__(self, message_type: type[IMessage], handler_type: HandlerType) -> None:
        self.message_type = message_type
        self.handler_type = handler_type

    def __str__(self) -> str:
        return f'{self.handler_type.__name__} already registered for {self.message_type.__name__}'


class MultipleHandlersRegistered(ImproperlyConfiguredError):  # noqa: N818
    def __init__(self, message_type: type[IMessage]) -> None:
        self.message_type = message_type

    def __str__(self) -> str:
        return f'Multiple handlers registered for {self.message_type.__name__}, invoke() requires exactly one'
