from __future__ import annotations

import inspect
import typing
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from lattice.modules import Module

if TYPE_CHECKING:
    from collections.abc import Sequence


class _A:
    def __init__(self, b: str) -> None:
        self.b = b


def iter_submodules(module: Module) -> Iterable[Module]:
    yield module
    yield from module.imports


def provider_dependencies(provider: object) -> Sequence[type[Any]]:
    if inspect.isclass(provider):
        provider = provider.__init__

    params = typing.get_type_hints(provider)
    params.pop('return', None)
    return tuple(params.values())


def test_module() -> None:
    b = Module('b', providers=[str])
    a = Module('a', providers=[_A], imports=[b])

    for module in [a, b]:
        for provider in module.providers:
            for dependency in provider_dependencies(provider=provider):
                assert any(dependency in imported_module.providers for imported_module in iter_submodules(module))
