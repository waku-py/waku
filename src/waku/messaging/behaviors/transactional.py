from __future__ import annotations

import logging
from typing import Any

from waku.messaging.contracts.pipeline import CallNext, IPipelineBehavior

# Runtime import: dishka introspects __init__ type hints at container-build time
# (get_type_hints), so this DI-injected type must resolve at runtime — not under TYPE_CHECKING.
from waku.uow import IUnitOfWork  # noqa: TC001

logger = logging.getLogger(__name__)


class TransactionalBehavior(IPipelineBehavior[Any, Any]):
    __slots__ = ('_uow',)

    def __init__(self, uow: IUnitOfWork) -> None:
        self._uow = uow

    async def handle(self, _message: Any, /, call_next: CallNext[Any]) -> Any:
        try:
            result = await call_next()
        except Exception:
            await self._safe_rollback()
            raise
        try:
            await self._uow.commit()
        except Exception:
            await self._safe_rollback()
            raise
        return result

    async def _safe_rollback(self) -> None:
        try:
            await self._uow.rollback()
        except Exception:
            logger.exception('Rollback failed')
