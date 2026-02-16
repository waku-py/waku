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
        if '-' in self.stream_type:
            msg = f'StreamId stream_type must not contain hyphens: {self.stream_type!r}'
            raise ValueError(msg)
        if not self.stream_key:
            msg = 'StreamId stream_key cannot be empty'
            raise ValueError(msg)

    @classmethod
    def for_aggregate(cls, aggregate_type: str, aggregate_id: str) -> StreamId:
        return cls(stream_type=aggregate_type, stream_key=aggregate_id)

    @classmethod
    def from_value(cls, value: str) -> StreamId:
        stream_type, sep, stream_key = value.partition('-')
        if not sep or not stream_type or not stream_key:
            msg = f"Invalid stream ID format: {value!r}. Expected '{{stream_type}}-{{stream_key}}'"
            raise ValueError(msg)
        return cls(stream_type=stream_type, stream_key=stream_key)

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
