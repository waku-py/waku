from __future__ import annotations

from typing import TYPE_CHECKING, Any

from waku.serialization.exceptions import UpcasterChainError

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from waku.serialization.upcasting.interfaces import IPayloadUpcaster

__all__ = ['UpcasterChain']


class UpcasterChain:
    """Per-event-type ordered upcasters that migrate a stored payload up to the current schema version."""

    __slots__ = ('_chains',)

    def __init__(self, upcasters_by_type: Mapping[str, Sequence[IPayloadUpcaster]]) -> None:
        chains: dict[str, tuple[IPayloadUpcaster, ...]] = {}
        for event_type, upcasters in upcasters_by_type.items():
            sorted_upcasters = sorted(upcasters, key=lambda u: u.from_version)
            seen: set[int] = set()
            for u in sorted_upcasters:
                if u.from_version < 1:
                    msg = f'Invalid from_version {u.from_version} for event type {event_type!r}: must be >= 1'
                    raise UpcasterChainError(msg)
                if u.from_version in seen:
                    msg = f'Duplicate upcaster for event type {event_type!r} at from_version {u.from_version}'
                    raise UpcasterChainError(msg)
                seen.add(u.from_version)
            chains[event_type] = tuple(sorted_upcasters)
        self._chains = chains

    def upcast(self, event_type: str, data: dict[str, Any], schema_version: int) -> dict[str, Any]:
        upcasters = self._chains.get(event_type)
        if not upcasters:
            return data
        if schema_version > upcasters[-1].from_version:
            return data
        for u in upcasters:
            if u.from_version >= schema_version:
                data = u.upcast(data)
        return data
