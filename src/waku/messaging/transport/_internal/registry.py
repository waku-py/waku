from __future__ import annotations

from typing import TYPE_CHECKING, Any

from waku.exceptions import ImproperlyConfiguredError

if TYPE_CHECKING:
    from collections.abc import Collection, Iterable, Mapping

    from waku.messaging.transport.interfaces import IEnvelopeMapper, IListener, ISender, ITransport

__all__ = [
    'TransportRegistry',
    'resolve_default_scheme',
    'split_destination',
]


def split_destination(uri: str, *, default_scheme: str | None) -> tuple[str, str]:
    """Parse ``uri`` into ``(scheme, queue)``.

    ``'rabbitmq://orders'`` → ``('rabbitmq', 'orders')``.
    Bare ``'orders'`` → ``(default_scheme, 'orders')`` when a default is provided.

    Raises:
        ImproperlyConfiguredError: Bare URI with no ``default_scheme`` given, or a URI
            missing a scheme or a queue (e.g. ``'rabbitmq://'``).
    """
    if '://' in uri:
        scheme, queue = uri.split('://', 1)
    elif default_scheme is None:
        msg = (
            f'Cannot resolve bare destination {uri!r}: no default scheme is set. '
            'Register exactly one transport or pass default_scheme explicitly.'
        )
        raise ImproperlyConfiguredError(msg)
    else:
        scheme, queue = default_scheme, uri
    if not scheme or not queue:
        msg = f"transport destination {uri!r} needs both a scheme and a queue (e.g. 'rabbitmq://orders')."
        raise ImproperlyConfiguredError(msg)
    return scheme, queue


def resolve_default_scheme(schemes: Collection[str], *, explicit: str | None = None) -> str | None:
    """Effective default scheme: explicit > sole-transport implicit > None."""
    if explicit is not None:
        return explicit
    if len(schemes) == 1:
        return next(iter(schemes))
    return None


class TransportRegistry:
    """Named transports with scheme-based dispatch.

    When ``default_scheme`` is omitted and exactly one transport is registered, that sole scheme is the implicit
    default for bare URIs.
    """

    __slots__ = ('_default_scheme', '_external_mappers', '_transports')

    def __init__(
        self,
        transports: Mapping[str, ITransport],
        *,
        default_scheme: str | None = None,
        external_mappers: Mapping[str, IEnvelopeMapper[Any, Any]] | None = None,
    ) -> None:
        self._transports: dict[str, ITransport] = dict(transports)
        self._default_scheme: str | None = resolve_default_scheme(self._transports, explicit=default_scheme)
        self._external_mappers: dict[str, IEnvelopeMapper[Any, Any]] = dict(external_mappers or {})
        self._validate_mapper_families()

    def sender_for(self, uri: str) -> ISender:
        """Raises ``ImproperlyConfiguredError`` for unknown or unresolvable schemes."""
        return self._resolve(uri)

    def mapper_for(self, uri: str) -> IEnvelopeMapper[Any, Any] | None:
        """Return the per-route mapper override for *uri*, or ``None`` if none is configured.

        Direct dict lookup — ``message.destination`` is byte-identical to the configured
        ``BrokerEndpointEntry.uri`` (the outbox writes ``destination=endpoint.uri`` verbatim; do NOT
        normalise or scheme-split here).
        """
        return self._external_mappers.get(uri)

    def listener_for(self, uri: str) -> IListener:
        """Raises ``ImproperlyConfiguredError`` for unknown or unresolvable schemes."""
        return self._resolve(uri)

    def transports(self) -> Iterable[ITransport]:
        return self._transports.values()

    @property
    def default_scheme(self) -> str | None:
        """Effective default: explicit > sole-transport implicit > None."""
        return self._default_scheme

    def _resolve(self, uri: str) -> ITransport:
        scheme, _ = split_destination(uri, default_scheme=self._default_scheme)
        transport = self._transports.get(scheme)
        if transport is None:
            msg = f'no transport registered for scheme {scheme!r} (uri={uri!r}).'
            raise ImproperlyConfiguredError(msg)
        return transport

    def _validate_mapper_families(self) -> None:
        # Startup check: a wrong-family per-endpoint mapper (e.g. a Kafka mapper on a rabbitmq://
        # endpoint) would otherwise surface only as a dispatch-time AttributeError swallowed into
        # retry/DLQ. Each transport self-describes its accepted family via ITransport.mapper_family.
        for uri, mapper in self._external_mappers.items():
            transport = self._resolve(uri)
            family = transport.mapper_family
            if not isinstance(mapper, family):
                scheme = split_destination(uri, default_scheme=self._default_scheme)[0]
                msg = (
                    f'Envelope mapper {type(mapper).__name__} configured for {uri!r} does not match '
                    f'the {scheme!r} transport: expected an instance of {family.__name__}.'
                )
                raise ImproperlyConfiguredError(msg)
