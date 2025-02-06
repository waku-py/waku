from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

    from waku.di import Provider
    from waku.extensions import ModuleExtension
    from waku.modules._metadata import DynamicModule, ModuleMetadata, ModuleType


class Module:
    def __init__(self, module_type: ModuleType, metadata: ModuleMetadata) -> None:
        self.id: Final[UUID] = metadata.id
        self.target: Final[ModuleType] = module_type
        self.distance: Final[int] = sys.maxsize if metadata.is_global else 0

        self.providers: Final[Sequence[Provider[Any]]] = metadata.providers
        self.imports: Final[Sequence[ModuleType | DynamicModule]] = metadata.imports
        self.exports: Final[Sequence[object | ModuleType]] = metadata.exports
        self.extensions: Final[Sequence[ModuleExtension]] = metadata.extensions
        self.is_global: Final[bool] = metadata.is_global

    @property
    def name(self) -> str:
        return self.target.__name__

    def __str__(self) -> str:
        return self.__repr__()

    def __repr__(self) -> str:
        return f'Module[{self.name}]'

    def __hash__(self) -> int:
        return hash(self.id)

    def __lt__(self, other: Module) -> bool:
        return self.distance < other.distance
