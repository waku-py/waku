from __future__ import annotations

from waku.eventsourcing.exceptions import EventSourcingError, UpcasterChainError


def test_upcaster_chain_error_is_event_sourcing_error() -> None:
    error = UpcasterChainError('duplicate from_version')
    assert isinstance(error, EventSourcingError)
    assert str(error) == 'duplicate from_version'
