from waku.eventsourcing import EventSourcingConfig
from waku.eventsourcing.serialization.json import JsonEventSerializer

es_config = EventSourcingConfig(
    event_serializer=JsonEventSerializer,
)
