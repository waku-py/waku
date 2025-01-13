from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final, TypeVar, cast

from waku.modules.types import HasModuleMetadata, ModuleMetadata, ModuleType

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from waku.di import Provider
    from waku.extensions import ModuleExtension

T = TypeVar('T')

MODULE_METADATA_KEY: Final = '__module_metadata__'


def module(
    *,
    providers: Sequence[Provider[Any]] = (),
    imports: Sequence[ModuleType] = (),
    exports: Sequence[object | ModuleType] = (),
    extensions: Sequence[ModuleExtension] = (),
    is_global: bool = False,
) -> Callable[[type[T]], type[T]]:
    def decorator(cls: type[T]) -> type[T]:
        metadata = ModuleMetadata(
            providers=list(providers),
            imports=list(imports),
            exports=list(exports),
            extensions=list(extensions),
            is_global=is_global,
            target=cast(type[HasModuleMetadata], cls),
        )
        setattr(cls, MODULE_METADATA_KEY, metadata)
        return cls

    return decorator
