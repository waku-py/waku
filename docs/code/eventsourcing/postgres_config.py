from waku.eventsourcing import EventSourcingConfig
from waku.eventsourcing.serialization.json import JsonEventSerializer
from waku.eventsourcing.store.sqlalchemy.store import make_sqlalchemy_event_store

from app.database import tables

es_config = EventSourcingConfig(
    store_factory=make_sqlalchemy_event_store(tables),
    event_serializer=JsonEventSerializer,
)
