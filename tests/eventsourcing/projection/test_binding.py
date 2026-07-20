from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from typing_extensions import override

from waku.eventsourcing.exceptions import EventSourcingConfigError
from waku.eventsourcing.projection.binding import CatchUpProjectionBinding
from waku.eventsourcing.projection.interfaces import ICatchUpProjection

if TYPE_CHECKING:
    from collections.abc import Sequence

    from waku.eventsourcing.contracts.event import StoredEvent


class _Projection(ICatchUpProjection):
    projection_name = 'binding_test'

    @override
    async def project(self, events: Sequence[StoredEvent], /) -> None:  # pragma: no cover
        pass


@pytest.mark.parametrize(
    ('kwargs', 'match'),
    [
        pytest.param({'batch_size': 0}, r'batch_size.*idle', id='batch_size_zero'),
        pytest.param({'batch_size': -1}, r'batch_size', id='batch_size_negative'),
        pytest.param({'max_retry_attempts': -1}, r'max_retry_attempts', id='max_retry_attempts_negative'),
        pytest.param({'base_retry_delay_seconds': -1}, r'base_retry_delay_seconds', id='base_retry_delay_negative'),
        pytest.param({'max_retry_delay_seconds': 0}, r'max_retry_delay_seconds', id='max_retry_delay_zero'),
        pytest.param(
            {'base_retry_delay_seconds': 10.0, 'max_retry_delay_seconds': 5.0},
            r'max_retry_delay_seconds.*base_retry_delay_seconds',
            id='max_retry_delay_below_base',
        ),
        pytest.param({'gap_timeout_seconds': 0}, r'gap_timeout_seconds', id='gap_timeout_zero'),
    ],
)
def test_degenerate_config_raises(kwargs: dict[str, Any], match: str) -> None:
    with pytest.raises(EventSourcingConfigError, match=match):
        CatchUpProjectionBinding(projection=_Projection, **kwargs)


@pytest.mark.parametrize(
    'kwargs',
    [
        pytest.param({'batch_size': 1}, id='batch_size_one'),
        pytest.param({'max_retry_attempts': 0}, id='max_retry_attempts_zero'),
        pytest.param({'base_retry_delay_seconds': 0}, id='base_retry_delay_zero'),
        pytest.param(
            {'base_retry_delay_seconds': 10.0, 'max_retry_delay_seconds': 10.0},
            id='max_retry_delay_equals_base',
        ),
    ],
)
def test_valid_boundary_constructs(kwargs: dict[str, Any]) -> None:
    binding = CatchUpProjectionBinding(projection=_Projection, **kwargs)

    assert binding.projection is _Projection


def test_defaults_construct() -> None:
    binding = CatchUpProjectionBinding(projection=_Projection)

    assert binding.batch_size == 100
    assert binding.max_retry_attempts == 0
    assert binding.base_retry_delay_seconds == 10.0
    assert binding.max_retry_delay_seconds == 300.0
    assert binding.gap_timeout_seconds == 10.0
