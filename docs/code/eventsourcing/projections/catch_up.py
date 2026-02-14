from collections.abc import Sequence

from waku.eventsourcing.contracts.event import StoredEvent
from waku.eventsourcing.projection.interfaces import ErrorPolicy, ICatchUpProjection


class AccountSummaryProjection(ICatchUpProjection):
    projection_name = 'account_summary'
    error_policy = ErrorPolicy.RETRY

    async def project(self, events: Sequence[StoredEvent], /) -> None:
        for event in events:
            print(f'Processing {event.event_type} at position {event.global_position}')

    async def teardown(self) -> None:
        print('Cleaning up projection state for rebuild')
