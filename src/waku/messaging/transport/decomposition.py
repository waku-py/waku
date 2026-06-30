from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from waku.messages import MessageIdentity
from waku.messaging.contracts.envelope import MessageEnvelope
from waku.messaging.transport.interfaces import EnvelopeMetadata

if TYPE_CHECKING:
    from waku.messaging.errors.dead_letter import DeadLetterEntry
    from waku.messaging.identity import MessageTypeRegistry
    from waku.messaging.inbox.models import InboxEntry
    from waku.messaging.outbox.models import OutboxMessage
    from waku.serialization.codec import PayloadCodec

__all__ = [
    'encode_metadata',
    'encode_payload',
    'envelope_metadata_of',
    'rebuild_envelope',
    'wire_metadata_from_entry',
]


def encode_payload(envelope: MessageEnvelope[Any], codec: PayloadCodec) -> dict[str, Any]:
    """Return the codec-encoded payload dict for *envelope*.

    This is the ``'payload'`` field value only — envelope metadata is captured
    separately via :func:`envelope_metadata_of`.
    """
    return codec.encode(envelope.payload, type(envelope.payload))


def encode_metadata(envelope: MessageEnvelope[Any]) -> dict[str, Any]:
    """Return the ``metadata_`` persistence dict for *envelope*.

    Carries the five non-column envelope fields: ``message_version``, ``timestamp``,
    ``headers``, ``scheduled_time``, and ``expires_at``.  Key names and datetime
    format (ISO 8601 string) MUST match what :func:`_parse_metadata_json` reads so
    that :func:`wire_metadata_from_entry` reconstructs the values correctly.

    Typed columns (correlation_id, causation_id, group_id, message_type) are stored
    directly on the row and are intentionally excluded here.
    """
    return {
        'message_version': envelope.message_version,
        'timestamp': envelope.timestamp.isoformat(),
        'headers': dict(envelope.headers),
        'scheduled_time': envelope.scheduled_time.isoformat() if envelope.scheduled_time is not None else None,
        'expires_at': envelope.expires_at.isoformat() if envelope.expires_at is not None else None,
    }


def envelope_metadata_of(envelope: MessageEnvelope[Any]) -> EnvelopeMetadata:
    """Extract all non-payload fields from *envelope* into an :class:`EnvelopeMetadata`.

    In-memory peer of :func:`encode_metadata` (the persistence dict): where ``encode_metadata``
    serialises to a JSONB blob, this function preserves datetime objects for the wire/transport layer.
    UUIDs are stringified; ``timestamp``/``scheduled_time``/``expires_at`` remain as
    ``datetime`` objects — isoformatting happens at the persistence or wire boundary.

    Note: this is the in-memory construction path used by tests and inbound producers.
    Production reconstruction from persisted rows goes through :func:`wire_metadata_from_entry`;
    reconstruction from broker headers goes through :func:`waku.messaging.transport.mapping.metadata_from_headers`.
    """
    return EnvelopeMetadata(
        message_id=str(envelope.message_id),
        correlation_id=str(envelope.correlation_id),
        causation_id=str(envelope.causation_id),
        message_type=envelope.message_type,
        message_version=envelope.message_version,
        timestamp=envelope.timestamp,
        headers=dict(envelope.headers),
        group_id=envelope.group_id,
        scheduled_time=envelope.scheduled_time,
        expires_at=envelope.expires_at,
    )


def rebuild_envelope(
    payload: dict[str, Any],
    metadata: EnvelopeMetadata,
    codec: PayloadCodec,
    type_registry: MessageTypeRegistry,
) -> MessageEnvelope[Any]:
    """Reconstruct a :class:`MessageEnvelope` from a raw payload dict and its metadata.

    * ``type_registry.resolve_type`` raises :exc:`ValueError` for unknown message types.
    * All datetimes are normalised to UTC via ``.astimezone(UTC)``.
    * ``scheduled_time`` and ``expires_at`` are ``None``-guarded.
    * This function is intended for *healthy* rows whose ``timestamp`` is always present;
      if ``metadata.timestamp`` is ``None`` a :exc:`ValueError` is raised.

    Args:
        payload: Codec-encoded payload dict (output of :func:`encode_payload`).
        metadata: Non-payload envelope fields (output of :func:`envelope_metadata_of`
            or produced by persistence/transport mappers).
        codec: Used to decode the payload via the upcaster chain.
        type_registry: Maps the wire ``message_type`` name back to a Python type.

    Returns:
        A fully populated :class:`MessageEnvelope`.

    Raises:
        ValueError: If ``metadata.message_type`` is not registered, or if
            ``metadata.timestamp`` is ``None``.
    """
    payload_type = type_registry.resolve_type(metadata.message_type)

    if metadata.timestamp is None:
        msg = 'rebuild_envelope requires a non-None timestamp in metadata'
        raise ValueError(msg)

    identity = MessageIdentity(name=metadata.message_type, version=metadata.message_version)
    decoded: Any = codec.decode(payload, payload_type, identity)

    return MessageEnvelope(
        message_id=UUID(metadata.message_id),
        correlation_id=UUID(metadata.correlation_id),
        causation_id=UUID(metadata.causation_id),
        message_type=metadata.message_type,
        message_version=metadata.message_version,
        timestamp=metadata.timestamp.astimezone(UTC),
        payload=decoded,
        headers=metadata.headers,
        group_id=metadata.group_id,
        scheduled_time=(metadata.scheduled_time.astimezone(UTC) if metadata.scheduled_time is not None else None),
        expires_at=(metadata.expires_at.astimezone(UTC) if metadata.expires_at is not None else None),
    )


def _iso(value: Any) -> datetime | None:
    """Parse an isoformat string to datetime, or return None if absent."""
    return datetime.fromisoformat(value) if value is not None else None


def _parse_metadata_json(
    raw: dict[str, Any],
) -> tuple[int, datetime | None, dict[str, str], datetime | None, datetime | None]:
    """Parse the ``metadata_`` JSONB dict into its component fields.

    Returns ``(message_version, timestamp, headers, scheduled_time, expires_at)``.
    Each field is parsed independently: a corrupt ``timestamp`` falls back to ``None`` without
    losing a valid ``message_version`` or ``headers`` (which would cause wrong upcasting).
    Never raises.
    """
    try:
        version = int(raw.get('message_version', 1))
    except (TypeError, ValueError):
        version = 1

    try:
        hdrs = dict(raw.get('headers', {}))
    except (TypeError, ValueError):
        hdrs = {}

    try:
        ts = _iso(raw.get('timestamp'))
    except (TypeError, ValueError):
        ts = None

    try:
        sched = _iso(raw.get('scheduled_time'))
    except (TypeError, ValueError):
        sched = None

    try:
        exp = _iso(raw.get('expires_at'))
    except (TypeError, ValueError):
        exp = None

    return version, ts, hdrs, sched, exp


def wire_metadata_from_entry(entry: OutboxMessage | InboxEntry | DeadLetterEntry) -> EnvelopeMetadata:
    """Reconstruct an :class:`EnvelopeMetadata` from a persisted store entry.

    Typed columns (correlation_id, causation_id, group_id, message_type) are the single source of
    truth and are always read directly from the entry. The ``metadata_`` JSONB field carries the
    remaining envelope fields (message_version, timestamp, headers, scheduled_time, expires_at).

    ``entry.message_id`` is a uniform accessor across all three entry types: :class:`OutboxMessage`
    (``UUID(idempotency_key)``), :class:`InboxEntry` (``id``), :class:`DeadLetterEntry` (``message_id``
    column). No ``isinstance`` discriminator is needed.

    Fault-tolerant: a ``None`` or unparsable ``metadata_`` returns a minimal
    :class:`EnvelopeMetadata` built from typed columns only — never raises. This ensures the
    poison/quarantine path can still read correlation/causation even for malformed rows.
    """
    # entry.message_id is a uniform accessor: UUID for OutboxMessage/InboxEntry, UUID|None for DeadLetterEntry.
    # For legacy DLQ rows written before the message_id column existed, fall back to the entry's own id.
    raw_message_id = entry.message_id
    message_id = str(raw_message_id) if raw_message_id is not None else str(entry.id)
    # Legacy rows with NULL correlation_id/causation_id fall back to str(entry.id) so rebuild_envelope
    # receives a valid UUID string rather than '' (which crashes UUID('')).
    correlation_id = str(entry.correlation_id) if entry.correlation_id is not None else str(entry.id)
    causation_id = str(entry.causation_id) if entry.causation_id is not None else str(entry.id)

    message_version = 1
    timestamp: datetime | None = None
    headers: dict[str, str] = {}
    scheduled_time: datetime | None = None
    expires_at: datetime | None = None

    raw = entry.metadata_
    if raw is not None:
        message_version, timestamp, headers, scheduled_time, expires_at = _parse_metadata_json(raw)

    return EnvelopeMetadata(
        message_id=message_id,
        correlation_id=correlation_id,
        causation_id=causation_id,
        message_type=entry.message_type,
        group_id=entry.group_id,
        message_version=message_version,
        timestamp=timestamp,
        headers=headers,
        scheduled_time=scheduled_time,
        expires_at=expires_at,
    )
