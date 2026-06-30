"""Broker-agnostic envelope ⇄ header projection (Wolverine wire format).

Two public functions:
- ``wire_headers_of`` — project an ``EnvelopeMetadata`` into a flat ``dict[str, str]`` ready for broker headers.
- ``metadata_from_headers`` — reconstruct ``EnvelopeMetadata`` from incoming broker headers.

The mapping rules follow Wolverine's wire convention:
- Reserved fields are always projected under their bare names.
- User-supplied headers are emitted bare (no ``h.`` prefix) and silently dropped when the key collides with a
  reserved field (reserved wins).
- ``content-type`` is projected outbound and consumed (not echoed) inbound.

``UnsupportedContentTypeError`` is INTERNAL — not exported from ``waku.messaging``; inbound adapters import it
directly from this module.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from waku.messaging.transport.interfaces import EnvelopeMetadata

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = [
    'WIRE_CONTENT_TYPE',
    'UnsupportedContentTypeError',
    'metadata_from_headers',
    'wire_headers_of',
]

WIRE_CONTENT_TYPE = 'application/json'

# Keys owned by the framework — a user header under any of these names is silently dropped (reserved wins).
_RESERVED_KEYS: frozenset[str] = frozenset({
    'message_id',
    'correlation_id',
    'causation_id',
    'message_type',
    'message_version',
    'timestamp',
    'scheduled_time',
    'expires_at',
    'content-type',
    'group_id',
})


class UnsupportedContentTypeError(Exception):
    """Raised by ``metadata_from_headers`` when the inbound content-type is not ``application/json``.

    The JSON codec cannot decode a foreign/binary body — the inbound adapter should REJECT the message.
    Not exported from ``waku.messaging``; import from ``waku.messaging.transport.mapping``.
    """

    def __init__(self, content_type: str) -> None:
        self.content_type = content_type
        super().__init__(f'Unsupported content-type {content_type!r}; expected {WIRE_CONTENT_TYPE!r}')


def wire_headers_of(metadata: EnvelopeMetadata) -> dict[str, str]:
    """Project ``EnvelopeMetadata`` onto broker wire headers (Wolverine two-tier format).

    Tier 1 — always emitted (bare names):
        ``message_id``, ``correlation_id``, ``causation_id``, ``message_type``, ``message_version``,
        ``content-type`` (always ``application/json``).

    Tier 2 — emitted only when not ``None``:
        ``timestamp``, ``scheduled_time``, ``expires_at`` (ISO-8601 strings), ``group_id``.

    User headers — each key copied bare, SKIPPING any key present in ``_RESERVED_KEYS``.
    The reserved-field projection wins; the user value is silently dropped.
    """
    out: dict[str, str] = {
        'message_id': metadata.message_id,
        'correlation_id': metadata.correlation_id,
        'causation_id': metadata.causation_id,
        'message_type': metadata.message_type,
        'message_version': str(metadata.message_version),
        'content-type': WIRE_CONTENT_TYPE,
    }
    if metadata.timestamp is not None:
        out['timestamp'] = metadata.timestamp.isoformat()
    if metadata.scheduled_time is not None:
        out['scheduled_time'] = metadata.scheduled_time.isoformat()
    if metadata.expires_at is not None:
        out['expires_at'] = metadata.expires_at.isoformat()
    if metadata.group_id is not None:
        out['group_id'] = metadata.group_id
    out.update({key: value for key, value in metadata.headers.items() if key not in _RESERVED_KEYS})
    return out


def _parse_header_dt(headers: Mapping[str, str], key: str) -> datetime | None:
    raw = headers.get(key)
    return datetime.fromisoformat(raw) if raw is not None else None


def metadata_from_headers(headers: Mapping[str, str]) -> EnvelopeMetadata:
    """Reconstruct ``EnvelopeMetadata`` from inbound broker headers (Wolverine wire format).

    Content-type awareness (tier b):
    - A foreign (non-JSON) ``content-type`` raises ``UnsupportedContentTypeError`` so the inbound adapter can REJECT.
    - An absent ``content-type`` is lenient — the JSON codec is assumed.
    - ``content-type`` is consumed, not echoed into ``metadata.headers``.

    ``message_version`` is cast to ``int``; a non-numeric value falls back silently to ``1`` (prevents the upcaster
    chain from silently skipping messages with a malformed version header).

    Raises:
        UnsupportedContentTypeError: When the inbound ``content-type`` header is present and not
            ``application/json``.
    """
    content_type = headers.get('content-type', WIRE_CONTENT_TYPE)
    if content_type != WIRE_CONTENT_TYPE:
        raise UnsupportedContentTypeError(content_type)

    try:
        message_version = int(headers.get('message_version', 1))
    except ValueError:
        message_version = 1

    user_headers = {key: value for key, value in headers.items() if key not in _RESERVED_KEYS}

    return EnvelopeMetadata(
        message_id=headers.get('message_id', ''),
        correlation_id=headers.get('correlation_id', ''),
        causation_id=headers.get('causation_id', ''),
        message_type=headers.get('message_type', ''),
        message_version=message_version,
        timestamp=_parse_header_dt(headers, 'timestamp'),
        scheduled_time=_parse_header_dt(headers, 'scheduled_time'),
        expires_at=_parse_header_dt(headers, 'expires_at'),
        group_id=headers.get('group_id'),
        headers=user_headers,
    )
