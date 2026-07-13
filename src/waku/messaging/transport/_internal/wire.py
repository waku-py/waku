from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

from adaptix.load_error import LoadError

from waku._internal.retort import default_retort
from waku.messages import MessageIdentity
from waku.messaging.contracts.envelope import MessageEnvelope
from waku.messaging.transport.interfaces import EnvelopeMetadata, MalformedMetadataError

if TYPE_CHECKING:
    from waku.messaging._internal.identity import MessageTypeRegistry
    from waku.messaging.errors.dead_letter import DeadLetterEntry
    from waku.messaging.inbox.models import InboxEntry
    from waku.messaging.outbox.models import OutboxMessage
    from waku.serialization.codec import PayloadCodec

__all__ = [
    'WireMetadata',
    'encode_metadata',
    'encode_payload',
    'envelope_metadata_of',
    'rebuild_envelope',
    'wire_metadata_from_entry',
]


@dataclass(frozen=True, slots=True, kw_only=True)
class WireMetadata:
    """The six non-column envelope fields carried in the persisted ``metadata`` JSONB blob.

    Single authority for both directions: ``default_retort.dump`` writes the blob and
    ``default_retort.load`` reads it back. The typed columns (message_id, correlation_id,
    causation_id, message_type, group_id) live on the row and are never carried here.
    """

    message_version: int = 1
    timestamp: datetime | None = None
    headers: dict[str, str] = field(default_factory=dict)
    scheduled_time: datetime | None = None
    expires_at: datetime | None = None
    tenant_id: str | None = None


def encode_payload(envelope: MessageEnvelope[Any], codec: PayloadCodec) -> dict[str, Any]:
    """Return the codec-encoded payload dict for *envelope*.

    This is the ``'payload'`` field value only — envelope metadata is captured
    separately via :func:`envelope_metadata_of`.
    """
    return codec.encode(envelope.payload, type(envelope.payload))


def encode_metadata(envelope: MessageEnvelope[Any]) -> dict[str, Any]:
    """Return the ``metadata`` persistence dict for *envelope*.

    Carries the six non-column envelope fields (``message_version``, ``timestamp``, ``headers``,
    ``scheduled_time``, ``expires_at``, ``tenant_id``) by dumping a :class:`WireMetadata` through
    ``default_retort`` — the single serialization authority that :func:`wire_metadata_from_entry`
    loads it back with. Datetimes become ISO-8601 strings.

    Typed columns (correlation_id, causation_id, group_id, message_type) are stored directly on the
    row and are intentionally excluded here.
    """
    wire = WireMetadata(
        message_version=envelope.message_version,
        timestamp=envelope.timestamp,
        headers=dict(envelope.headers),
        scheduled_time=envelope.scheduled_time,
        expires_at=envelope.expires_at,
        tenant_id=envelope.tenant_id,
    )
    return cast('dict[str, Any]', default_retort.dump(wire, WireMetadata))


def envelope_metadata_of(envelope: MessageEnvelope[Any]) -> EnvelopeMetadata:
    """Extract all non-payload fields from *envelope* into an :class:`EnvelopeMetadata`.

    In-memory peer of :func:`encode_metadata` (the persistence dict): where ``encode_metadata``
    serialises to a JSONB blob, this function preserves datetime objects for the wire/transport layer.
    ``message_id`` (a ``UUID``) is stringified; ``correlation_id``/``causation_id`` are already
    free-form ``str``; ``timestamp``/``scheduled_time``/``expires_at`` remain as
    ``datetime`` objects — isoformatting happens at the persistence or wire boundary.

    Note: this is the in-memory construction path used by tests and inbound producers.
    Production reconstruction from persisted rows goes through :func:`wire_metadata_from_entry`;
    reconstruction from broker headers goes through :func:`waku.messaging.transport.mapping.metadata_from_headers`.
    """
    return EnvelopeMetadata(
        message_id=str(envelope.message_id),
        correlation_id=envelope.correlation_id,
        causation_id=envelope.causation_id,
        message_type=envelope.message_type,
        message_version=envelope.message_version,
        timestamp=envelope.timestamp,
        headers=dict(envelope.headers),
        group_id=envelope.group_id,
        tenant_id=envelope.tenant_id,
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
        correlation_id=metadata.correlation_id,
        causation_id=metadata.causation_id,
        message_type=metadata.message_type,
        message_version=metadata.message_version,
        timestamp=metadata.timestamp.astimezone(UTC),
        payload=decoded,
        headers=metadata.headers,
        group_id=metadata.group_id,
        tenant_id=metadata.tenant_id,
        scheduled_time=(metadata.scheduled_time.astimezone(UTC) if metadata.scheduled_time is not None else None),
        expires_at=(metadata.expires_at.astimezone(UTC) if metadata.expires_at is not None else None),
    )


def _load_wire_metadata(raw: dict[str, Any] | None) -> WireMetadata:
    """Load the persisted ``metadata`` blob into a :class:`WireMetadata`.

    A ``None`` blob (the metadata column is nullable) yields all defaults — the *absent* case is
    tolerated. A present-but-corrupt blob (wrong-typed or undeserializable field) is poison, never a
    silent coercion.

    Raises:
        MalformedMetadataError: If *raw* is present but wrong-typed or undeserializable.
    """
    if raw is None:
        return WireMetadata()
    try:
        return default_retort.load(raw, WireMetadata)
    except LoadError as exc:
        msg = 'persisted metadata blob is corrupt or undeserializable'
        raise MalformedMetadataError(msg) from exc


def wire_metadata_from_entry(entry: OutboxMessage | InboxEntry | DeadLetterEntry) -> EnvelopeMetadata:
    """Reconstruct an :class:`EnvelopeMetadata` from a persisted store entry.

    Typed columns (correlation_id, causation_id, group_id, message_type) are the single source of
    truth and are always read directly from the entry. The ``metadata`` JSONB field carries the
    remaining envelope fields via :class:`WireMetadata`.

    ``entry.message_id`` is a uniform accessor across all three entry types: :class:`OutboxMessage`
    (``UUID(idempotency_key)``), :class:`InboxEntry` (``id``), :class:`DeadLetterEntry` (``message_id``
    column). No ``isinstance`` discriminator is needed.

    A ``None`` metadata blob yields a minimal :class:`EnvelopeMetadata` (typed columns + defaults). A
    present-but-corrupt blob raises :exc:`MalformedMetadataError` so the caller can quarantine the row.
    """
    # message_id coalesce (load-bearing in the current schema): OutboxMessage.message_id is UUID|None
    # (None when idempotency_key is not a UUID, e.g. a foreign row) and DeadLetterEntry.message_id is a
    # nullable column — fall back to the entry's own id so rebuild receives a valid UUID string.
    raw_message_id = entry.message_id
    message_id = str(raw_message_id) if raw_message_id is not None else str(entry.id)
    # correlation/causation coalesce (load-bearing for InboxEntry, whose columns are nullable str|None):
    # fall back to str(entry.id) so rebuild_envelope receives a valid UUID string rather than '' (UUID('')
    # crashes). No-op for Outbox/DLQ, whose columns are required str.
    correlation_id = entry.correlation_id if entry.correlation_id is not None else str(entry.id)
    causation_id = entry.causation_id if entry.causation_id is not None else str(entry.id)

    wire = _load_wire_metadata(entry.metadata)

    return EnvelopeMetadata(
        message_id=message_id,
        correlation_id=correlation_id,
        causation_id=causation_id,
        message_type=entry.message_type,
        group_id=entry.group_id,
        tenant_id=wire.tenant_id,
        message_version=wire.message_version,
        timestamp=wire.timestamp,
        headers=wire.headers,
        scheduled_time=wire.scheduled_time,
        expires_at=wire.expires_at,
    )
