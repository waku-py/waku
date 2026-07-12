from __future__ import annotations

from sqlalchemy.dialects.postgresql import insert

# Runtime import: dishka introspects __init__ type hints at container-build time (get_type_hints),
# so this DI-injected type must resolve at runtime — not under TYPE_CHECKING.
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: TC002
from typing_extensions import override

from waku.backends.sqlalchemy.sequence.tables import message_sequences_table
from waku.messaging.partition import ISequenceAllocator

__all__ = ['SqlAlchemySequenceAllocator']

_t = message_sequences_table


class SqlAlchemySequenceAllocator(ISequenceAllocator):
    __slots__ = ('_session',)

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @override
    async def allocate(self, group_id: str) -> int:
        # INSERT ... ON CONFLICT DO UPDATE takes a row-level lock on `message_sequences(group_id)`:
        # concurrent allocations for the SAME group serialize on that lock, so no two committed rows
        # in a group share a sequence number and none can be skipped. The store never commits — the
        # scope owner co-commits this allocation with the entry insert.
        stmt = (
            insert(_t)
            .values(group_id=group_id, last_sequence=1)
            .on_conflict_do_update(
                index_elements=[_t.c.group_id],
                set_={'last_sequence': _t.c.last_sequence + 1},
            )
            .returning(_t.c.last_sequence)
        )
        result = await self._session.execute(stmt)
        next_sequence: int = result.scalar_one()
        return next_sequence
