from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from waku.eventsourcing.exceptions import SnapshotConfigNotFoundError
from waku.eventsourcing.snapshot.migration import _EMPTY_CHAIN, SnapshotMigrationChain

if TYPE_CHECKING:
    from collections.abc import Mapping

    from waku.eventsourcing.snapshot.interfaces import ISnapshotStrategy

__all__ = [
    'SnapshotConfig',
    'SnapshotConfigRegistry',
]


@dataclass(frozen=True, slots=True, kw_only=True)
class SnapshotConfig:
    strategy: ISnapshotStrategy
    schema_version: int = 1
    migration_chain: SnapshotMigrationChain = field(default=_EMPTY_CHAIN)


class SnapshotConfigRegistry:
    __slots__ = ('_configs',)

    def __init__(self, configs: Mapping[str, SnapshotConfig]) -> None:
        self._configs = dict(configs)

    def get(self, aggregate_name: str) -> SnapshotConfig:
        config = self._configs.get(aggregate_name)
        if config is None:
            raise SnapshotConfigNotFoundError(aggregate_name)
        return config
