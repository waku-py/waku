from __future__ import annotations


def test_upcasting_types_exported_from_package() -> None:
    from waku.eventsourcing import (  # noqa: F401, PLC0415
        FnUpcaster,
        IEventUpcaster,
        UpcasterChain,
        UpcasterChainError,
        add_field,
        noop,
        remove_field,
        rename_field,
        upcast,
    )
