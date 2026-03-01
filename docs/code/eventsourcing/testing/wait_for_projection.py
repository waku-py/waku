from waku.eventsourcing.projection.in_memory import InMemoryCheckpointStore
from waku.eventsourcing.projection.registry import CatchUpProjectionRegistry
from waku.eventsourcing.store.in_memory import InMemoryEventStore
from waku.eventsourcing.testing import wait_for_all_projections, wait_for_projection


async def test_wait_for_single_projection(
    event_store: InMemoryEventStore,
    checkpoint_store: InMemoryCheckpointStore,
) -> None:
    # ... append events via command handler ...

    await wait_for_projection(
        checkpoint_store=checkpoint_store,
        event_reader=event_store,
        projection_name='account_summary',
        deadline=5.0,
    )

    # Projection is caught up — assert read model state


async def test_wait_for_all(
    event_store: InMemoryEventStore,
    checkpoint_store: InMemoryCheckpointStore,
    projection_registry: CatchUpProjectionRegistry,
) -> None:
    # ... append events via command handler ...

    await wait_for_all_projections(
        checkpoint_store=checkpoint_store,
        event_reader=event_store,
        projection_registry=projection_registry,
        deadline=10.0,
    )

    # All projections caught up — assert read model state
