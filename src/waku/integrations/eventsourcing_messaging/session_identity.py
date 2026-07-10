from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from typing_extensions import override

from waku.di import is_registered
from waku.eventsourcing.projection.interfaces import ICheckpointStore
from waku.eventsourcing.snapshot.interfaces import ISnapshotStore
from waku.eventsourcing.store.interfaces import IEventStore
from waku.exceptions import ImproperlyConfiguredError
from waku.extensions import AfterApplicationInit
from waku.messaging.outbox.interfaces import IOutboxStore
from waku.uow import IUnitOfWork

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from waku.application import WakuApplication

__all__ = ['StoreSessionIdentityExtension']

# Stores that must commit on the SAME AsyncSession as the UoW for atomic append+forward, in check
# order. Each entry is (interface, human label); a registered store that exposes no session is skipped.
_SESSION_STORES: tuple[tuple[type[Any], str], ...] = (
    (IEventStore, 'event store'),
    (ISnapshotStore, 'snapshot store'),
    (ICheckpointStore, 'checkpoint store'),
    (IOutboxStore, 'outbox store'),
)


@runtime_checkable
class _SupportsSession(Protocol):
    @property
    def session(self) -> AsyncSession: ...


class StoreSessionIdentityExtension(AfterApplicationInit):
    """Fail fast when a persistence store and the UoW do not share one AsyncSession.

    Atomic append+forward holds only if the event-store append, the snapshot/checkpoint writes, the
    outbox write, and the UoW commit all run on the SAME session within a request scope; a mis-wire
    that hands any store a different AsyncSession silently splits its writes from the commit. This turns
    that silent split into a startup error, covering the event store, snapshot store, checkpoint store,
    and outbox store.

    Session identity is sqla-specific — only the SqlAlchemy stores / ``SqlAlchemyUnitOfWork`` expose a
    ``session``. Stores/UoWs that do not (e.g. ``InMemoryEventStore``, a non-sqla UoW, a fake store)
    cannot prove identity and are skipped (documented no-op).

    Unlike the messaging presence checks (which only call ``is_registered``), this extension must
    ``get`` the stores and UoW to compare their session identity — it intentionally pays that boot-time
    construction cost. Keep the ``get`` calls; do not "harmonize" them to a presence-only check.
    """

    __slots__ = ()

    @override
    async def after_app_init(self, app: WakuApplication) -> None:
        # Check + resolve inside a request scope: IUnitOfWork is typically scoped (one session per
        # request) and is not resolvable at app scope. No UoW configured -> nothing to validate.
        async with app.container() as scope:
            if not await is_registered(scope, IUnitOfWork):
                return
            uow = await scope.get(IUnitOfWork)
            if not isinstance(uow, _SupportsSession):
                return
            for interface, label in _SESSION_STORES:
                if not await is_registered(scope, interface):
                    continue
                store = await scope.get(interface)
                if not isinstance(store, _SupportsSession):
                    continue
                if store.session is not uow.session:
                    msg = (
                        f'{label} ({interface.__name__}) and IUnitOfWork must share one AsyncSession for '
                        'atomic append+forward, but they resolved to different sessions. Register a single '
                        'scoped(AsyncSession) used by both (e.g. waku.messaging.sqla.shared_session).'
                    )
                    raise ImproperlyConfiguredError(msg)
