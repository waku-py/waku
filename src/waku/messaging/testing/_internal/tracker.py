import enum
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, TypeVar
from uuid import UUID

import anyio
from typing_extensions import override

from waku.messages import IMessage
from waku.messaging.contracts.envelope import MessageEnvelope
from waku.messaging.endpoints.outcome import ExecutionOutcome
from waku.messaging.handler import MessageHandler
from waku.messaging.observability.observer import IMessageObserver

__all__ = [
    'MessageTracker',
    'TrackedEnvelope',
    'TrackingEvent',
    'TrackingMessageObserver',
]

_MessageT = TypeVar('_MessageT', bound=IMessage)


class TrackingEvent(enum.Enum):
    """Which lifecycle hook produced a :class:`TrackedEnvelope`."""

    SENT = 'SENT'
    EXECUTED = 'EXECUTED'


@dataclass(frozen=True, slots=True)
class TrackedEnvelope:
    """One recorded observation of a message. ``SENT`` records leave the execution fields ``None``."""

    event: TrackingEvent
    message_id: UUID
    message_type: str
    destination: str
    payload: IMessage
    outcome: ExecutionOutcome | None = None
    exc: Exception | None = None
    duration: timedelta | None = None
    handler_type: type[MessageHandler[Any, Any]] | None = None


@dataclass(slots=True)
class _Condition:
    predicate: Callable[[Sequence[TrackedEnvelope]], bool]
    event: anyio.Event


class MessageTracker:
    """In-process sink recording message ``on_sent``/``on_executed`` observations for test assertions.

    Fed by a :class:`TrackingMessageObserver` on the ``IMessageObserver`` seam; make output-based assertions on
    the recorded envelopes (``sent``/``executed``/``single``) and ``await`` sleep-free for a message of type ``T``
    to be sent or executed (``wait_for_sent``/``wait_for_executed``).

    Register it as ``singleton(MessageTracker)`` and declare the observer BEFORE the container is built (in
    ``MessagingConfig.observers`` or an endpoint's ``observers=``); the observer collection is materialised once at
    ``make_async_container`` time, so ``container.get(MessageTracker)`` returns the same instance the observer
    writes to. It does NOT compose with ``override()``, which patches an already-built container and cannot
    contribute a new member to the already-materialised observer collection.

    APP-scoped and single-use: records accumulate for the app's whole lifetime with no reset. Use one app (one
    ``create_test_app``) per tracked activity; reusing an app pollutes a second activity's counts and ``single``
    with the first activity's records.
    """

    __slots__ = ('_conditions', '_records')

    def __init__(self) -> None:
        self._records: list[TrackedEnvelope] = []
        self._conditions: list[_Condition] = []

    @property
    def sent(self) -> Sequence[TrackedEnvelope]:
        """All recorded ``SENT`` observations, in arrival order."""
        return tuple(record for record in self._records if record.event is TrackingEvent.SENT)

    @property
    def executed(self) -> Sequence[TrackedEnvelope]:
        """All recorded ``EXECUTED`` observations, in arrival order."""
        return tuple(record for record in self._records if record.event is TrackingEvent.EXECUTED)

    @property
    def exceptions(self) -> Sequence[Exception]:
        """Every exception carried by a recorded observation, in arrival order."""
        return tuple(record.exc for record in self._records if record.exc is not None)

    def executed_of(self, message_type: type[IMessage]) -> Sequence[TrackedEnvelope]:
        """The ``EXECUTED`` records whose payload is an instance of ``message_type`` (subclass-tolerant)."""
        return tuple(record for record in self.executed if isinstance(record.payload, message_type))

    def single(self, message_type: type[_MessageT]) -> _MessageT:
        """The sole recorded payload of ``message_type``, deduped by ``message_id``.

        A normal send-then-execute flow records both a ``SENT`` and an ``EXECUTED`` envelope for one message, so
        the match is deduped by ``message_id`` before the count check.

        Raises:
            ValueError: If zero or more than one distinct message of ``message_type`` was recorded.
        """
        by_id: dict[UUID, _MessageT] = {}
        for record in self._records:
            if isinstance(record.payload, message_type):
                by_id[record.message_id] = record.payload
        payloads = list(by_id.values())
        if len(payloads) != 1:
            msg = f'Expected exactly one recorded {message_type.__name__}, found {len(payloads)}.\n{self.describe()}'
            raise ValueError(msg)
        return payloads[0]

    def describe(self) -> str:
        """A compact dump of every recorded observation, used in wait-timeout messages."""
        if not self._records:
            return 'MessageTracker: no records.'
        lines = [
            f'  {record.event.value:<8} {record.message_type} id={record.message_id} dest={record.destination} '
            f'outcome={record.outcome.value if record.outcome is not None else "-"}'
            for record in self._records
        ]
        return 'MessageTracker records:\n' + '\n'.join(lines)

    async def wait_for_executed(
        self,
        message_type: type[IMessage],
        *,
        count: int = 1,
        outcome: ExecutionOutcome | None = None,
        deadline: float = 5.0,
    ) -> Sequence[TrackedEnvelope]:
        """Await ``count`` distinct executions of ``message_type`` (default: any terminal outcome).

        Returns as soon as ``count`` distinct ``message_id``s have executed (or immediately if already satisfied);
        ``outcome=`` narrows to a single terminal outcome.

        Raises:
            TimeoutError: If ``count`` distinct executions do not arrive within *deadline* seconds; the message
                carries an activity dump of every recorded observation.
        """
        outcome_desc = f', outcome={outcome.value}' if outcome is not None else ''
        description = f'executed {message_type.__name__} (count>={count}{outcome_desc})'

        def select(records: Sequence[TrackedEnvelope]) -> Sequence[TrackedEnvelope]:
            return [
                record
                for record in records
                if record.event is TrackingEvent.EXECUTED
                and isinstance(record.payload, message_type)
                and (outcome is None or record.outcome is outcome)
            ]

        return await self._await_count(select, count, deadline, description)

    async def wait_for_sent(
        self,
        message_type: type[IMessage],
        *,
        count: int = 1,
        deadline: float = 5.0,
    ) -> Sequence[TrackedEnvelope]:
        """Await ``count`` distinct ``SENT`` observations of ``message_type``.

        Raises:
            TimeoutError: If ``count`` distinct observations do not arrive within *deadline* seconds.
        """
        description = f'sent {message_type.__name__} (count>={count})'

        def select(records: Sequence[TrackedEnvelope]) -> Sequence[TrackedEnvelope]:
            return [
                record
                for record in records
                if record.event is TrackingEvent.SENT and isinstance(record.payload, message_type)
            ]

        return await self._await_count(select, count, deadline, description)

    async def _await_count(
        self,
        select: Callable[[Sequence[TrackedEnvelope]], Sequence[TrackedEnvelope]],
        count: int,
        deadline: float,
        description: str,
    ) -> Sequence[TrackedEnvelope]:
        def satisfied(records: Sequence[TrackedEnvelope]) -> bool:
            return len({record.message_id for record in select(records)}) >= count

        # Immediate-satisfy: evaluate against already-recorded envelopes first. The check, event creation and
        # append below contain NO await, so on a single cooperative event loop they cannot interleave with a
        # concurrent hook's _record — hence no lock is needed (in-process invariant; NG-1). Revisit with an
        # anyio.Lock only if a future hook must await mid-section.
        if satisfied(self._records):
            return tuple(select(self._records))
        condition = _Condition(satisfied, anyio.Event())
        self._conditions.append(condition)
        try:
            with anyio.fail_after(deadline):
                await condition.event.wait()
        except TimeoutError:
            msg = f'Timed out waiting for {description}.\n{self.describe()}'
            raise TimeoutError(msg) from None
        finally:
            if condition in self._conditions:
                self._conditions.remove(condition)
        return tuple(select(self._records))

    def _record(self, record: TrackedEnvelope) -> None:
        # Append then re-evaluate every pending condition, waking any now-satisfied waiter. No await here (P5):
        # keeps the append+evaluate critical section interleave-free without a lock. Event.set() is idempotent.
        self._records.append(record)
        for condition in self._conditions:
            if not condition.event.is_set() and condition.predicate(self._records):
                condition.event.set()


class TrackingMessageObserver(IMessageObserver):
    """``IMessageObserver`` that forwards ``on_sent``/``on_executed`` hooks into an injected :class:`MessageTracker`.

    Declare it in ``MessagingConfig.observers`` (global) or an endpoint's ``observers=`` (scoped); DI injects the
    APP-scoped ``MessageTracker`` singleton the test also resolves.
    """

    __slots__ = ('_tracker',)

    def __init__(self, tracker: MessageTracker) -> None:
        self._tracker = tracker

    @override
    async def on_sent(self, envelope: MessageEnvelope[Any], destination: str) -> None:
        self._tracker._record(  # noqa: SLF001 -- observer is the tracker's sanctioned same-module recorder
            TrackedEnvelope(
                event=TrackingEvent.SENT,
                message_id=envelope.message_id,
                message_type=envelope.message_type,
                destination=destination,
                payload=envelope.payload,
            ),
        )

    @override
    async def on_executed(
        self,
        envelope: MessageEnvelope[Any],
        destination: str,
        handler_type: type[MessageHandler[Any, Any]],
        outcome: ExecutionOutcome,
        exc: Exception | None,
        duration: timedelta,
    ) -> None:
        self._tracker._record(  # noqa: SLF001 -- observer is the tracker's sanctioned same-module recorder
            TrackedEnvelope(
                event=TrackingEvent.EXECUTED,
                message_id=envelope.message_id,
                message_type=envelope.message_type,
                destination=destination,
                payload=envelope.payload,
                outcome=outcome,
                exc=exc,
                duration=duration,
                handler_type=handler_type,
            ),
        )
