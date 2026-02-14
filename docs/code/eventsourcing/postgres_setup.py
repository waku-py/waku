from collections.abc import AsyncIterator

from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from waku.eventsourcing.store.sqlalchemy.tables import bind_event_store_tables

DATABASE_URL = 'postgresql+psycopg://user:password@localhost:5432/mydb'

metadata = MetaData()
tables = bind_event_store_tables(metadata)
engine = create_async_engine(DATABASE_URL, echo=False)


async def create_session(engine_: AsyncEngine) -> AsyncIterator[AsyncSession]:
    async with AsyncSession(engine_, expire_on_commit=False) as session:
        yield session
