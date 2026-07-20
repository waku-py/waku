import os
from collections.abc import AsyncIterator

from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

DATABASE_URL = os.environ['DATABASE_URL']

metadata = MetaData()
engine = create_async_engine(DATABASE_URL, echo=False)


async def create_session(engine_: AsyncEngine) -> AsyncIterator[AsyncSession]:
    async with AsyncSession(engine_, expire_on_commit=False) as session:
        yield session  # noqa: ASYNC119
