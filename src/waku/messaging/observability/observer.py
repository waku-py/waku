import logging
from abc import ABC
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import timedelta
from functools import partial
from typing import Any, Final

from waku.messaging.contracts.envelope import MessageEnvelope
from waku.messaging.contracts.handler import HandlerType
from waku.messaging.endpoints.executor import ExecutionOutcome

__all__ = ['INVOKE_DESTINATION', 'IMessageObserver', 'MessageObservers', 'ObserverPlan']

logger = logging.getLogger(__name__)

INVOKE_DESTINATION: Final[str] = 'invoke://inline'
"""Reserved destination reported to the execution hooks for ``bus.invoke()`` (inline, endpoint-less)
executions. The ``invoke`` scheme is reserved: no transport or endpoint may register it."""


class IMessageObserver(ABC):  # noqa: B024 -- intentionally no abstract method: every hook is opt-in
    """Side-channel observer of message lifecycle. Implement only the events you need (defaults no-op).

    Implementations MUST NOT raise (the fan-out swallows, but raising wastes work). The envelope is read-only.
    Hooks are NOT guaranteed to pair: an expired message is discarded with a terminal ``on_executed``
    (``DISCARDED``, zero duration) and no preceding ``on_executing``.

    Register a GLOBAL observer (fires on every message, including ``bus.invoke()``) via
    ``MessagingConfig.observers``; register a PER-ENDPOINT observer (fires only for that endpoint's events,
    never on the endpoint-less ``invoke`` path) via the ``observers=`` kwarg on ``listen``/``local_queue``/
    ``external_endpoint``.

    Observers are APP-scoped singletons shared across all endpoints and concurrently-executing messages, so
    implementations must be stateless or thread/async-safe — unlike per-message pipeline behaviors.
    """

    __slots__ = ()

    async def on_sent(self, envelope: MessageEnvelope[Any], destination: str) -> None:  # noqa: B027
        """Default no-op; override to observe a message being sent/routed to ``destination``.

        ``sent`` means ACCEPTED FOR DELIVERY, not delivered: durable endpoints fire after their enqueue
        commit, but the external (outbox) endpoint fires inside the caller's still-open transaction — a
        later rollback means no delivery despite this event — and the relay's wire-send is unobserved.
        """

    async def on_executing(  # noqa: B027
        self,
        envelope: MessageEnvelope[Any],
        destination: str,
        handler_type: HandlerType,
    ) -> None:
        """Default no-op; override to observe a handler about to execute on ``destination``."""

    async def on_executed(  # noqa: B027
        self,
        envelope: MessageEnvelope[Any],
        destination: str,
        handler_type: HandlerType,
        outcome: ExecutionOutcome,
        exc: Exception | None,
        duration: timedelta,
    ) -> None:
        """Default no-op; override to observe a handler's terminal outcome on ``destination``."""


class MessageObservers:
    """Fans a lifecycle event out to all observers, swallowing per-observer failure.

    Observability must never affect processing — this is the single sanctioned broad-catch home.

    Null-object consistency (house doctrine). An injected/optional collaborator is resolved to a null default in
    place of ``None``, and its consumer fans out or calls through with NO per-call absence guard. This class is
    the pattern's reference: an empty ``observers`` tuple makes every ``sent``/``executing``/``executed`` fan-out
    a no-op loop. The same shape recurs — ``Endpoint.pause``/``resume`` default to no-ops (only buffered/durable
    override), ``RoutingTable.resolve`` returns ``()`` for unrouted types, and each optional messaging collaborator
    (circuit breaker, dead-letter store, invoke-path unit of work, listener backpressure) resolves to a null
    default so its consumer never branches on absence.
    """

    __slots__ = ('_observers',)

    def __init__(self, observers: Sequence[IMessageObserver]) -> None:
        self._observers = tuple(observers)

    async def sent(self, envelope: MessageEnvelope[Any], destination: str) -> None:
        for obs in self._observers:
            await self._safe(partial(obs.on_sent, envelope, destination), obs)

    async def executing(self, envelope: MessageEnvelope[Any], destination: str, handler_type: HandlerType) -> None:
        for obs in self._observers:
            await self._safe(partial(obs.on_executing, envelope, destination, handler_type), obs)

    async def executed(
        self,
        envelope: MessageEnvelope[Any],
        destination: str,
        handler_type: HandlerType,
        outcome: ExecutionOutcome,
        exc: Exception | None,
        duration: timedelta,
    ) -> None:
        for obs in self._observers:
            await self._safe(partial(obs.on_executed, envelope, destination, handler_type, outcome, exc, duration), obs)

    @staticmethod
    async def _safe(hook: Callable[[], Awaitable[None]], observer: IMessageObserver) -> None:
        try:
            await hook()
        except Exception:  # sanctioned: an observer fault must never break message processing (ruff BLE001
            # does not fire here since the traceback is logged via exc_info, so no noqa is needed/accepted)
            logger.warning('Message observer %s failed', type(observer).__name__, exc_info=True)


class ObserverPlan:
    """Memoized per-endpoint observer composition (global ∪ endpoint-declared), built once at DI bootstrap."""

    __slots__ = ('_by_uri', '_global')

    def __init__(self, global_observers: MessageObservers, by_uri: Mapping[str, MessageObservers]) -> None:
        self._global = global_observers
        self._by_uri = dict(by_uri)

    @property
    def global_observers(self) -> MessageObservers:
        return self._global

    def for_endpoint(self, uri: str) -> MessageObservers:
        return self._by_uri.get(uri, self._global)
