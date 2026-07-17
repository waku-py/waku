from waku.di import AsyncContainer
from waku.eventsourcing.projection import InMemoryLease
from waku.eventsourcing.projection.runner import CatchUpProjectionRunner


async def run_projections(container: AsyncContainer) -> None:
    runner = await CatchUpProjectionRunner.create(
        container=container,
        lock=InMemoryLease(),
    )
    await runner.run()
