from waku.di import AsyncContainer
from waku.eventsourcing.projection.runner import CatchUpProjectionRunner


async def run_projections(container: AsyncContainer) -> None:
    runner = await CatchUpProjectionRunner.create(container=container)
    await runner.run()
