from __future__ import annotations

from typing import TYPE_CHECKING, Self

from waku.messaging.exceptions import HandlerAlreadyRegistered, MapFrozenError

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

    from waku.messages import IMessage
    from waku.messaging.contracts.handler import HandlerType

__all__ = ['HandlerMap']


class HandlerMap:
    __slots__ = ('_frozen', '_registry')

    def __init__(self) -> None:
        self._registry: dict[type[IMessage], list[HandlerType]] = {}
        self._frozen = False

    def freeze(self) -> None:
        self._frozen = True

    def bind(self, message_type: type[IMessage], handler_type: HandlerType) -> Self:
        if self._frozen:
            raise MapFrozenError
        existing = self._registry.setdefault(message_type, [])
        if handler_type in existing:
            raise HandlerAlreadyRegistered(message_type, handler_type)
        existing.append(handler_type)
        return self

    def merge(self, other: HandlerMap) -> Self:
        if self._frozen:
            raise MapFrozenError
        for message_type, handler_types in other._registry.items():
            for handler_type in handler_types:
                self.bind(message_type, handler_type)
        return self

    def get_handler_types(self, message_type: type[IMessage]) -> Sequence[HandlerType]:
        return tuple(self._registry.get(message_type, ()))

    def handler_types(self) -> Iterator[HandlerType]:
        for handlers in self._registry.values():
            yield from handlers

    def message_types(self) -> Iterator[type[IMessage]]:
        yield from self._registry

    def items(self) -> Iterator[tuple[type[IMessage], tuple[HandlerType, ...]]]:
        for msg_type, handlers in self._registry.items():
            yield msg_type, tuple(handlers)

    def __bool__(self) -> bool:
        return bool(self._registry)
