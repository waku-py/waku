"""Extension registry for centralized management of extensions."""

from __future__ import annotations

import inspect
from collections import defaultdict
from typing import TYPE_CHECKING, Self, TypeVar, cast

from waku.extensions.protocols import ApplicationExtension, ModuleExtension

if TYPE_CHECKING:
    from waku.modules import ModuleType

__all__ = ['ExtensionRegistry']


_AppExtT = TypeVar('_AppExtT', bound=ApplicationExtension)
_ModExtT = TypeVar('_ModExtT', bound=ModuleExtension)


class ExtensionRegistry:
    """Registry for extensions.

    This registry maintains references to all extensions in the application,
    allowing for centralized management and discovery.
    """

    def __init__(self) -> None:
        self._app_extensions: dict[type[ApplicationExtension], list[ApplicationExtension]] = defaultdict(list)
        self._module_extensions: dict[ModuleType, list[ModuleExtension]] = defaultdict(list)

    def register_application_extension(self, extension: ApplicationExtension) -> Self:
        """Register an application extension with optional priority and tags."""
        ext_type = type(extension)
        extension_bases = [
            base
            for base in inspect.getmro(ext_type)
            if (isinstance(base, ApplicationExtension) and base != ext_type)  # type: ignore[unreachable]
        ]
        for base in extension_bases:
            self._app_extensions[cast('type[ApplicationExtension]', base)].append(extension)
        return self

    def register_module_extension(self, module_type: ModuleType, extension: ModuleExtension) -> Self:
        self._module_extensions[module_type].append(extension)
        return self

    def get_application_extensions(self, extension_type: type[_AppExtT]) -> list[_AppExtT]:
        return cast('list[_AppExtT]', self._app_extensions.get(cast('type[ApplicationExtension]', extension_type), []))

    def get_module_extensions(self, module_type: ModuleType, extension_type: type[_ModExtT]) -> list[_ModExtT]:
        extensions = cast('list[_ModExtT]', self._module_extensions.get(module_type, []))
        return [ext for ext in extensions if isinstance(ext, extension_type)]
