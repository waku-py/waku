from __future__ import annotations

import dataclasses
from typing import Any, cast
from uuid import UUID

from adaptix import Retort, dumper, loader
from typing_extensions import override

from waku.eventsourcing.serialization.interfaces import IEventSerializer
from waku.eventsourcing.serialization.registry import EventTypeRegistry  # noqa: TC001  # Dishka needs runtime access

__all__ = ['JsonEventSerializer']


class JsonEventSerializer(IEventSerializer):
    def __init__(self, registry: EventTypeRegistry) -> None:
        self._registry = registry
        self._retort = Retort(
            recipe=[
                loader(UUID, UUID),
                dumper(UUID, str),
            ],
        )

    @override
    def serialize(self, event: Any, /) -> dict[str, Any]:
        if not dataclasses.is_dataclass(event) or isinstance(event, type):
            msg = f'Expected a dataclass instance, got {type(event).__name__}'
            raise TypeError(msg)
        return cast('dict[str, Any]', self._retort.dump(event, type(event)))

    @override
    def deserialize(self, data: dict[str, Any], event_type: str, /) -> Any:
        cls = self._registry.resolve(event_type)
        return self._retort.load(data, cls)
