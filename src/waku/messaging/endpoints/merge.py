from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypeVar

from waku._internal.sentinel import MISSING
from waku.exceptions import ImproperlyConfiguredError
from waku.messaging.endpoints.base import BrokerEndpointEntry

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from waku.messaging.endpoints.aspects import ListenAspect, SendAspect
    from waku.messaging.endpoints.base import LocalQueueEntry
    from waku.messaging.partition import PartitionKeyExtractor
    from waku.messaging.transport.interfaces import IEnvelopeMapper

_T = TypeVar('_T')

__all__ = [
    'MergedBrokerEndpoint',
    'merge_broker_endpoints',
]


@dataclass(frozen=True, slots=True, kw_only=True)
class MergedBrokerEndpoint:
    uri: str
    mapper: IEnvelopeMapper[Any, Any] | None
    partition_by: PartitionKeyExtractor | None
    listen: ListenAspect | None
    send: SendAspect | None


def merge_broker_endpoints(
    entries: Sequence[LocalQueueEntry | BrokerEndpointEntry],
    *,
    inbox_configured: bool,
) -> tuple[MergedBrokerEndpoint, ...]:
    fragments_by_uri: dict[str, list[BrokerEndpointEntry]] = {}
    for entry in entries:
        if isinstance(entry, BrokerEndpointEntry):
            fragments_by_uri.setdefault(entry.uri, []).append(entry)

    return tuple(
        _merge_fragments(uri, fragments, inbox_configured=inbox_configured)
        for uri, fragments in fragments_by_uri.items()
    )


def _merge_fragments(
    uri: str,
    fragments: list[BrokerEndpointEntry],
    *,
    inbox_configured: bool,
) -> MergedBrokerEndpoint:
    mapper = _resolve_unique(uri, (fragment.mapper for fragment in fragments), 'conflicting envelope mappers')
    partition_by = _resolve_unique(uri, (fragment.partition_by for fragment in fragments), 'conflicting partition_by')
    listen = _resolve_aspect(uri, (fragment.listen for fragment in fragments), 'ListenAspect')
    send = _resolve_aspect(uri, (fragment.send for fragment in fragments), 'SendAspect')

    if listen is not None and not inbox_configured:
        msg = f"endpoint '{uri}' declares listen but no inbox is configured"
        raise ImproperlyConfiguredError(msg)
    if listen is None and send is None:
        msg = f"endpoint '{uri}' declares neither listen nor send"
        raise ImproperlyConfiguredError(msg)

    return MergedBrokerEndpoint(uri=uri, mapper=mapper, partition_by=partition_by, listen=listen, send=send)


def _resolve_unique(uri: str, values: Iterable[_T | MISSING], conflict_message: str) -> _T | None:  # type: ignore[valid-type]  # mypy lacks PEP 661 sentinel support; pyrefly narrows
    distinct: list[_T] = []
    for value in values:
        if value is MISSING:
            continue
        if all(value is not existing for existing in distinct):
            distinct.append(value)

    if len(distinct) > 1:
        msg = f"endpoint '{uri}': {conflict_message}"
        raise ImproperlyConfiguredError(msg)
    return distinct[0] if distinct else None


def _resolve_aspect(uri: str, aspects: Iterable[_T | None], aspect_name: str) -> _T | None:
    present = [aspect for aspect in aspects if aspect is not None]
    if len(present) > 1:
        msg = f"endpoint '{uri}': conflicting {aspect_name} declarations"
        raise ImproperlyConfiguredError(msg)
    return present[0] if present else None
