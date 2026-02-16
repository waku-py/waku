from __future__ import annotations

import dataclasses
from uuid import UUID

from adaptix import Retort, dumper, loader

from waku.eventsourcing.contracts.stream import StreamId

__all__ = ['shared_retort', 'validate_dataclass_instance']

shared_retort = Retort(
    recipe=[
        loader(UUID, UUID),
        dumper(UUID, str),
        loader(StreamId, StreamId.from_value),
        dumper(StreamId, str),
    ],
)


def validate_dataclass_instance(value: object) -> None:
    if not dataclasses.is_dataclass(value) or isinstance(value, type):
        msg = f'Expected a dataclass instance, got {type(value).__name__}'
        raise TypeError(msg)
