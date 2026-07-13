from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import timedelta
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, TypeAlias

from waku._internal.sentinel import MISSING
from waku.messaging.endpoints._internal.aspects import ListenAspect, SendAspect
from waku.messaging.endpoints.base import BrokerEndpointEntry, LocalQueueEntry

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from waku.messages import IMessage
    from waku.messaging.circuit_breaker.config import CircuitBreakerConfig
    from waku.messaging.contracts.handler import HandlerType
    from waku.messaging.endpoints._internal.merge import MergedBrokerEndpoint
    from waku.messaging.endpoints.base import Endpoint, EndpointMode
    from waku.messaging.inbox.backpressure import BufferingLimits
    from waku.messaging.observability.observer import IMessageObserver
    from waku.messaging.partition import PartitionKeyExtractor
    from waku.messaging.sending.policy import SendingFailurePolicy
    from waku.messaging.transport.interfaces import IEnvelopeMapper
    from waku.modules import ModuleType

__all__ = [
    'external_endpoint',
    'listen',
    'local_queue',
    'route',
    'route_module',
]

HandlerSubscriptions: TypeAlias = 'Mapping[type[IMessage], frozenset[HandlerType]]'


@dataclass(frozen=True, slots=True)
class RoutingTable:
    entries: tuple[MergedBrokerEndpoint | LocalQueueEntry, ...] = ()
    type_routes: Mapping[type[IMessage], tuple[str, ...]] = field(default_factory=lambda: MappingProxyType({}))
    endpoint_subscriptions: Mapping[str, HandlerSubscriptions] = field(default_factory=lambda: MappingProxyType({}))


class MessageRouter:
    __slots__ = ('_by_uri', '_endpoints', '_routes')

    def __init__(
        self,
        routes: Mapping[type[IMessage], Sequence[Endpoint]],
        endpoints: Sequence[Endpoint],
    ) -> None:
        self._routes = routes
        self._endpoints = endpoints
        self._by_uri = {endpoint.uri: endpoint for endpoint in endpoints}

    @property
    def endpoints(self) -> Sequence[Endpoint]:
        return self._endpoints

    def resolve(self, message_type: type[IMessage]) -> Sequence[Endpoint]:
        return self._routes.get(message_type, ())

    def endpoint_for(self, uri: str) -> Endpoint | None:
        return self._by_uri.get(uri)


@dataclass(frozen=True, slots=True)
class RouteDescriptor:
    message_type: type[IMessage]
    endpoint_uri: str


@dataclass(frozen=True, slots=True)
class ModuleRouteDescriptor:
    module_type: ModuleType
    endpoint_uri: str


class RouteBuilder:
    __slots__ = ('_message_type',)

    def __init__(self, message_type: type[IMessage]) -> None:
        self._message_type = message_type

    def to(self, endpoint_uri: str) -> RouteDescriptor:
        return RouteDescriptor(self._message_type, endpoint_uri)


class ModuleRouteBuilder:
    __slots__ = ('_module_type',)

    def __init__(self, module_type: ModuleType) -> None:
        self._module_type = module_type

    def to(self, endpoint_uri: str) -> ModuleRouteDescriptor:
        return ModuleRouteDescriptor(self._module_type, endpoint_uri)


def route(message_type: type[IMessage]) -> RouteBuilder:
    """Begin a per-type route; ``.to(uri)`` binds ``message_type`` to that endpoint.

    Args:
        message_type: The message type to route.

    Returns:
        A builder whose ``to(uri)`` completes the binding.
    """
    return RouteBuilder(message_type)


def route_module(module_type: ModuleType) -> ModuleRouteBuilder:
    """Begin a module-level route; ``.to(uri)`` binds every handler in the module to that endpoint.

    Args:
        module_type: The module whose handlers are routed.

    Returns:
        A builder whose ``to(uri)`` completes the binding.
    """
    return ModuleRouteBuilder(module_type)


def listen(
    uri: str,
    *,
    max_requeue_attempts: int | MISSING = MISSING,  # type: ignore[valid-type]  # mypy lacks PEP 661 sentinel support; pyrefly narrows
    circuit_breaker: CircuitBreakerConfig | MISSING | None = MISSING,  # type: ignore[valid-type]  # mypy lacks PEP 661 sentinel support; pyrefly narrows
    backpressure: BufferingLimits | None = None,
    mapper: IEnvelopeMapper[Any, Any] | MISSING = MISSING,  # type: ignore[valid-type]  # mypy lacks PEP 661 sentinel support; pyrefly narrows
    partition_by: PartitionKeyExtractor | MISSING = MISSING,  # type: ignore[valid-type]  # mypy lacks PEP 661 sentinel support; pyrefly narrows
    observers: Sequence[type[IMessageObserver]] = (),
) -> BrokerEndpointEntry:
    """Declare a broker endpoint that consumes messages from ``uri`` (the listen side).

    Returns:
        A broker endpoint entry carrying the listen aspect and per-endpoint overrides.
    """
    return BrokerEndpointEntry(
        uri=uri,
        mapper=mapper,
        partition_by=partition_by,
        listen=ListenAspect(
            max_requeue_attempts=max_requeue_attempts,
            circuit_breaker=circuit_breaker,
            backpressure=backpressure,
        ),
        observers=tuple(dict.fromkeys(observers)),
    )


def local_queue(  # noqa: PLR0913 -- one keyword per LocalQueueEntry field
    uri: str,
    *,
    mode: EndpointMode | MISSING = MISSING,  # type: ignore[valid-type]  # mypy lacks PEP 661 sentinel support; pyrefly narrows
    max_parallel: int = 1,
    stop_timeout: timedelta = timedelta(seconds=5),
    max_buffer_size: float = math.inf,
    partition_by: Callable[[IMessage], str | None] | None = None,
    circuit_breaker: CircuitBreakerConfig | MISSING | None = MISSING,  # type: ignore[valid-type]  # mypy lacks PEP 661 sentinel support; pyrefly narrows
    max_requeue_attempts: int | MISSING = MISSING,  # type: ignore[valid-type]  # mypy lacks PEP 661 sentinel support; pyrefly narrows
    observers: Sequence[type[IMessageObserver]] = (),
) -> LocalQueueEntry:
    """Declare an in-process local-queue endpoint served by a background worker.

    Returns:
        A local-queue endpoint entry with its buffering, parallelism, and stop-timeout settings.
    """
    return LocalQueueEntry(
        uri=uri,
        mode=mode,
        max_parallel=max_parallel,
        stop_timeout=stop_timeout,
        max_buffer_size=max_buffer_size,
        partition_by=partition_by,
        circuit_breaker=circuit_breaker,
        max_requeue_attempts=max_requeue_attempts,
        observers=tuple(dict.fromkeys(observers)),
    )


def external_endpoint(
    uri: str,
    *,
    sending_failure_policies: Sequence[SendingFailurePolicy] = (),
    mapper: IEnvelopeMapper[Any, Any] | MISSING = MISSING,  # type: ignore[valid-type]  # mypy lacks PEP 661 sentinel support; pyrefly narrows
    partition_by: PartitionKeyExtractor | MISSING = MISSING,  # type: ignore[valid-type]  # mypy lacks PEP 661 sentinel support; pyrefly narrows
    observers: Sequence[type[IMessageObserver]] = (),
) -> BrokerEndpointEntry:
    """Declare a send-only broker endpoint used as a dispatch target (the send side).

    Returns:
        A broker endpoint entry carrying the send aspect and per-endpoint overrides.
    """
    return BrokerEndpointEntry(
        uri=uri,
        mapper=mapper,
        partition_by=partition_by,
        send=SendAspect(sending_failure_policies=tuple(sending_failure_policies)),
        observers=tuple(dict.fromkeys(observers)),
    )
