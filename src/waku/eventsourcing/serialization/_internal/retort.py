from __future__ import annotations

import dataclasses

from adaptix import dumper, loader

from waku._internal.retort import default_retort
from waku.eventsourcing.contracts.stream import StreamId

__all__ = ['es_default_retort', 'validate_dataclass_instance']

es_default_retort = default_retort.extend(
    recipe=[
        loader(StreamId, StreamId.from_value),
        dumper(StreamId, str),
    ],
)


def validate_dataclass_instance(value: object) -> None:
    if not dataclasses.is_dataclass(value) or isinstance(value, type):
        msg = f'Expected a dataclass instance, got {type(value).__name__}'
        raise TypeError(msg)
