# Runtime imports: dishka introspects __init__ type hints at container-build time (get_type_hints),
# so the DI-injected param types must resolve at runtime (no `from __future__ import annotations`).
from typing_extensions import override

from waku.messaging.durability.interfaces import IDeadLetterStore, IDurabilityStore, IInboxStore, IOutboxStore

__all__ = ['DefaultDurabilityStore']


class DefaultDurabilityStore(IDurabilityStore):
    """Framework-agnostic composite: injects the three scoped facet ports a backend provides.

    Because each facet is the container-resolved port instance, ``store.outbox`` IS the scope's
    ``IOutboxStore`` (same object either way) — the spine every backend reuses for its
    ``IDurabilityStore`` registration.
    """

    __slots__ = ('_dead_letters', '_inbox', '_outbox')

    def __init__(self, outbox: IOutboxStore, inbox: IInboxStore, dead_letters: IDeadLetterStore) -> None:
        self._outbox = outbox
        self._inbox = inbox
        self._dead_letters = dead_letters

    @property
    @override
    def outbox(self) -> IOutboxStore:
        return self._outbox

    @property
    @override
    def inbox(self) -> IInboxStore:
        return self._inbox

    @property
    @override
    def dead_letters(self) -> IDeadLetterStore:
        return self._dead_letters
