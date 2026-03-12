from datetime import UTC, datetime
from typing import Any, cast

from adaptix import dumper, loader
from typing_extensions import override

from waku.messaging import IEvent
from waku.eventsourcing.serialization import EventTypeRegistry, IEventSerializer, default_retort


class UnixTimestampEventSerializer(IEventSerializer):
    def __init__(self, registry: EventTypeRegistry) -> None:
        self._registry = registry
        self._retort = default_retort.extend(
            recipe=[
                loader(datetime, lambda v: datetime.fromtimestamp(v, tz=UTC)),
                dumper(datetime, lambda v: int(v.timestamp())),
            ],
        )

    @override
    def serialize(self, event: IEvent, /) -> dict[str, Any]:
        return cast('dict[str, Any]', self._retort.dump(event, type(event)))

    @override
    def deserialize(self, data: dict[str, Any], event_type: str, /) -> IEvent:
        cls = self._registry.resolve(event_type)
        return self._retort.load(data, cls)
