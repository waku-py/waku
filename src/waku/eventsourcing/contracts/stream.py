from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TypeAlias

__all__ = [
    'AnyVersion',
    'Exact',
    'ExpectedVersion',
    'NoStream',
    'StreamExists',
    'StreamId',
    'StreamPosition',
]


@dataclass(frozen=True, slots=True)
class StreamId:
    stream_type: str
    stream_key: str

    def __post_init__(self) -> None:
        if not self.stream_type:
            msg = 'StreamId stream_type cannot be empty'
            raise ValueError(msg)
        if not self.stream_key:
            msg = 'StreamId stream_key cannot be empty'
            raise ValueError(msg)

    @classmethod
    def for_aggregate(cls, aggregate_type: str, aggregate_id: str) -> StreamId:
        return cls(stream_type=aggregate_type, stream_key=aggregate_id)

    @property
    def value(self) -> str:
        return f'{self.stream_type}-{self.stream_key}'

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class Exact:
    version: int


@dataclass(frozen=True, slots=True)
class NoStream:
    pass


@dataclass(frozen=True, slots=True)
class StreamExists:
    pass


@dataclass(frozen=True, slots=True)
class AnyVersion:
    pass


ExpectedVersion: TypeAlias = Exact | NoStream | StreamExists | AnyVersion


class StreamPosition(Enum):
    START = 'start'
    END = 'end'
