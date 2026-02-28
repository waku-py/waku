from __future__ import annotations

import pytest

from waku.eventsourcing.projection.registry import CatchUpProjectionRegistry

from tests.eventsourcing.projection.helpers import RecordingProjection, StopProjection, make_binding


def test_get_returns_binding_by_name() -> None:
    binding = make_binding(RecordingProjection)
    registry = CatchUpProjectionRegistry((binding,))

    assert registry.get('recording') is binding


def test_get_unknown_name_raises() -> None:
    registry = CatchUpProjectionRegistry(())

    with pytest.raises(ValueError, match="Projection 'nonexistent' not found"):
        registry.get('nonexistent')


def test_iter_yields_all_bindings() -> None:
    b1 = make_binding(RecordingProjection)
    b2 = make_binding(StopProjection)
    registry = CatchUpProjectionRegistry((b1, b2))

    assert list(registry) == [b1, b2]


def test_len() -> None:
    registry = CatchUpProjectionRegistry((make_binding(RecordingProjection),))

    assert len(registry) == 1


def test_empty_registry() -> None:
    registry = CatchUpProjectionRegistry(())

    assert len(registry) == 0
    assert list(registry) == []
