from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

    from waku import Module


def iter_submodules(*modules: Module) -> Iterator[Module]:
    submodules: set[Module] = set()
    stack = [*modules]
    while stack:
        submodule = stack.pop()
        if submodule in submodules:
            continue

        submodules.add(submodule)
        stack.extend(submodule.imports)

        yield submodule
