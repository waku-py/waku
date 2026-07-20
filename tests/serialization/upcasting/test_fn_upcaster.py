from __future__ import annotations

from waku.serialization.upcasting.fn import FnUpcaster


def test_fn_upcaster_applies_function() -> None:
    upcaster = FnUpcaster(from_version=1, fn=lambda data: {**data, 'new_field': True})

    result = upcaster.upcast({'existing': 'value'})

    assert result == {'existing': 'value', 'new_field': True}


def test_fn_upcaster_stores_from_version() -> None:
    upcaster = FnUpcaster(from_version=3, fn=lambda data: data)

    assert upcaster.from_version == 3
