from waku.di import AsyncContainer
from waku.eventsourcing.projection.lock.in_memory import InMemoryProjectionLock
from waku.eventsourcing.projection.runner import CatchUpProjectionRunner


async def run_projections(container: AsyncContainer) -> None:
    runner = await CatchUpProjectionRunner.create(
        container=container,
        lock=InMemoryProjectionLock(),
    )
    await runner.run()
