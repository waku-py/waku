from __future__ import annotations

from typing import TYPE_CHECKING

from typing_extensions import override

from waku.exceptions import ImproperlyConfiguredError, WakuError

if TYPE_CHECKING:
    from datetime import timedelta
    from uuid import UUID

    from waku._internal.node import NodeId
    from waku.messages import IMessage
    from waku.messaging.contracts.handler import HandlerType

__all__ = [
    'DeliveryOptionNotApplicableError',
    'DurabilityOwnershipLostError',
    'HandlerAlreadyRegisteredError',
    'HandlerNotFoundError',
    'HandlerTimeoutError',
    'InvalidDeliveryOptionsError',
    'MapFrozenError',
    'MessagingError',
    'MultipleHandlersRegisteredError',
    'NoRouteError',
    'RequeueBudgetExceededError',
    'SchedulingNotSupportedError',
]


class MessagingError(WakuError):
    pass


class MapFrozenError(MessagingError):
    def __init__(self) -> None:
        super().__init__('Cannot modify map after it is frozen')


class HandlerNotFoundError(MessagingError):
    def __init__(self, message_type: type[IMessage]) -> None:
        self.message_type = message_type

    @override
    def __str__(self) -> str:
        return f'No handler registered for {self.message_type.__name__}'


class HandlerTimeoutError(MessagingError):
    def __init__(self, message_id: UUID, deadline: timedelta | None) -> None:
        self.message_id = message_id
        self.deadline = deadline

    @override
    def __str__(self) -> str:
        return f'Handler timed out for message_id={self.message_id} after deadline={self.deadline}'


class RequeueBudgetExceededError(MessagingError):
    def __init__(self, message_id: UUID, attempts: int) -> None:
        self.message_id = message_id
        self.attempts = attempts

    @override
    def __str__(self) -> str:
        return f'Requeue budget exceeded for message_id={self.message_id} after {self.attempts} delivery attempt(s)'


class DurabilityOwnershipLostError(MessagingError):
    """A durable-row transition was refused: the acting node is no longer the row's recorded owner.

    A lost race, not a message failure. Recovery released the row because this node had left the
    registry, and a live node has claimed it since. The loser writes nothing and emits no terminal
    evidence — the successor owns the outcome.
    """

    def __init__(self, owner_id: NodeId, row_id: UUID, destination: str | None = None) -> None:
        self.owner_id = owner_id
        self.row_id = row_id
        self.destination = destination

    @override
    def __str__(self) -> str:
        row = f'{self.row_id}' if self.destination is None else f'{self.row_id}/{self.destination}'
        return f'node {self.owner_id} no longer owns durable row {row}'


class NoRouteError(MessagingError):
    def __init__(self, message_type: type[IMessage]) -> None:
        self.message_type = message_type

    @override
    def __str__(self) -> str:
        return (
            f'no endpoint routes {self.message_type.__name__!r}; in a single-process app use '
            f'invoke()/publish(), or add a route(...) for it'
        )


class InvalidDeliveryOptionsError(MessagingError):
    def __init__(self, reason: str) -> None:
        self.reason = reason

    @override
    def __str__(self) -> str:
        return f'Invalid delivery options: {self.reason}'


class DeliveryOptionNotApplicableError(MessagingError):
    def __init__(self, option: str, verb: str) -> None:
        self.option = option
        self.verb = verb

    @override
    def __str__(self) -> str:
        return f'Delivery option {self.option!r} is not applicable to {self.verb}()'


class SchedulingNotSupportedError(MessagingError):
    def __init__(self, uri: str) -> None:
        self.uri = uri

    @override
    def __str__(self) -> str:
        return f'endpoint {self.uri!r} does not support scheduled delivery'


class HandlerAlreadyRegisteredError(ImproperlyConfiguredError):
    def __init__(self, message_type: type[IMessage], handler_type: HandlerType) -> None:
        self.message_type = message_type
        self.handler_type = handler_type

    @override
    def __str__(self) -> str:
        return f'{self.handler_type.__name__} already registered for {self.message_type.__name__}'


class MultipleHandlersRegisteredError(ImproperlyConfiguredError):
    def __init__(self, message_type: type[IMessage]) -> None:
        self.message_type = message_type

    @override
    def __str__(self) -> str:
        return f'Multiple handlers registered for {self.message_type.__name__}, invoke() requires exactly one'
