from waku.di import AsyncContainer
from waku.eventsourcing.projection.config import CatchUpProjectionConfig
from waku.eventsourcing.projection.interfaces import ICatchUpProjection
from waku.eventsourcing.projection.lock.in_memory import InMemoryProjectionLock
from waku.eventsourcing.projection.runner import CatchUpProjectionRunner


async def run_projections(
    container: AsyncContainer,
    projection_types: list[type[ICatchUpProjection]],
) -> None:
    runner = CatchUpProjectionRunner(
        container=container,
        lock=InMemoryProjectionLock(),
        projection_types=projection_types,
        config=CatchUpProjectionConfig(
            batch_size=100,
            max_attempts=3,
        ),
    )
    await runner.run()
