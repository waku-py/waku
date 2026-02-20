from waku.di import AsyncContainer
from waku.eventsourcing.modules import CatchUpProjectionBinding
from waku.eventsourcing.projection.config import CatchUpProjectionConfig
from waku.eventsourcing.projection.lock.in_memory import InMemoryProjectionLock
from waku.eventsourcing.projection.runner import CatchUpProjectionRunner


async def run_projections(
    container: AsyncContainer,
    bindings: list[CatchUpProjectionBinding],
) -> None:
    runner = CatchUpProjectionRunner(
        container=container,
        lock=InMemoryProjectionLock(),
        bindings=bindings,
        config=CatchUpProjectionConfig(
            batch_size=100,
        ),
    )
    await runner.run()
