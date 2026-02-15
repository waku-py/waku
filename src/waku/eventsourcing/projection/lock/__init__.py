from waku.eventsourcing.projection.lock.in_memory import InMemoryProjectionLock
from waku.eventsourcing.projection.lock.interfaces import IProjectionLock

__all__ = [
    'IProjectionLock',
    'InMemoryProjectionLock',
]
