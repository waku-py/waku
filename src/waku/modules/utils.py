from __future__ import annotations

from dataclasses import replace
from typing import cast

from waku.modules.module import MODULE_METADATA_KEY
from waku.modules.types import DynamicModule, ModuleMetadata, ModuleType


def get_module_metadata(module: ModuleType) -> ModuleMetadata:
    if isinstance(module, DynamicModule):
        parent_module = cast(ModuleMetadata, getattr(module, MODULE_METADATA_KEY))
        return replace(
            parent_module,
            providers=[*parent_module.providers, *module.providers],
            imports=[*parent_module.imports, *module.imports],
            exports=[*parent_module.exports, *module.exports],
            extensions=[*parent_module.extensions, *module.extensions],
        )
    return cast(ModuleMetadata, getattr(module, MODULE_METADATA_KEY))
