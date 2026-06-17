from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from typing_extensions import override

from waku.di import is_registered
from waku.eventsourcing.store.interfaces import IEventStore
from waku.exceptions import ImproperlyConfiguredError
from waku.extensions import AfterApplicationInit
from waku.uow import IUnitOfWork

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from waku.application import WakuApplication

__all__ = ['StoreSessionIdentityExtension']


@runtime_checkable
class _SupportsSession(Protocol):
    @property
    def session(self) -> AsyncSession: ...


class StoreSessionIdentityExtension(AfterApplicationInit):
    """Fail fast when the event store and the UoW do not share one AsyncSession.

    Atomic append+forward holds only if the event-store append and the UoW commit run on the SAME
    session within a request scope; a mis-wire that hands the store a different AsyncSession silently
    splits the append from the commit/outbox. This turns that silent split into a startup error.

    Session identity is sqla-specific — only ``SqlAlchemyEventStore`` / ``SqlAlchemyUnitOfWork`` expose
    a ``session``. Stores/UoWs that do not (e.g. ``InMemoryEventStore``, a non-sqla UoW) cannot prove
    identity and are skipped (documented no-op); the messaging presence check still applies to them.

    Unlike the messaging presence checks (which only call ``is_registered``), this extension must
    ``get`` the store and UoW to compare their session identity — it intentionally pays that boot-time
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
            store = await scope.get(IEventStore)
            uow = await scope.get(IUnitOfWork)
            if not (isinstance(store, _SupportsSession) and isinstance(uow, _SupportsSession)):
                return
            if store.session is not uow.session:
                msg = (
                    'Event store and IUnitOfWork must share one AsyncSession for atomic append+forward, '
                    'but they resolved to different sessions. Register a single scoped(AsyncSession) used '
                    'by both (e.g. waku.messaging.sqla.shared_session).'
                )
                raise ImproperlyConfiguredError(msg)
