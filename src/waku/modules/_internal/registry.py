from __future__ import annotations

from typing import TYPE_CHECKING

from waku.exceptions import ImproperlyConfiguredError

if TYPE_CHECKING:
    from uuid import UUID

    from waku.di import BaseProvider
    from waku.modules._internal.metadata import DynamicModule, ModuleCompiler, ModuleType
    from waku.modules._internal.module import Module


__all__ = ['ModuleRegistry']


class ModuleRegistry:
    """Immutable registry for module queries and lookups."""

    def __init__(
        self,
        *,
        compiler: ModuleCompiler,
        root_module: Module,
        modules: dict[UUID, Module],
        providers: list[BaseProvider],
    ) -> None:
        self._compiler = compiler
        self._root_module = root_module
        self._modules = modules
        self._providers = tuple(providers)
        self._parent_to_module = self._build_parent_mapping(modules)

    @staticmethod
    def _build_parent_mapping(modules: dict[UUID, Module]) -> dict[type, Module]:
        """Build mapping from parent module classes to their registered DynamicModule instances.

        Raises:
            ImproperlyConfiguredError: If more than one registered module shares the same parent class.
        """
        mapping: dict[type, Module] = {}
        for mod in modules.values():
            if isinstance(mod.target, type):
                if mod.target in mapping:
                    msg = (
                        f'Multiple modules are registered for parent class {mod.target.__name__!r}; '
                        f'a parent class may back at most one registered module per application.'
                    )
                    raise ImproperlyConfiguredError(msg)
                mapping[mod.target] = mod
        return mapping

    @property
    def root_module(self) -> Module:
        return self._root_module

    @property
    def modules(self) -> tuple[Module, ...]:
        return tuple(self._modules.values())

    @property
    def providers(self) -> tuple[BaseProvider, ...]:
        return self._providers

    @property
    def compiler(self) -> ModuleCompiler:
        return self._compiler

    def get(self, module_type: ModuleType | DynamicModule) -> Module:
        # For plain module classes, check if they're registered via parent mapping first.
        # This handles the case where ConfigModule.register() was imported,
        # but ConfigModule (the class) is being exported.
        if isinstance(module_type, type) and module_type in self._parent_to_module:
            return self._parent_to_module[module_type]

        module_id = self.compiler.extract_metadata(module_type)[1].id
        return self.get_by_id(module_id)

    def get_by_id(self, module_id: UUID) -> Module:
        module = self._modules.get(module_id)
        if module is None:
            msg = f'Module with ID {module_id} is not registered in the graph.'
            raise KeyError(msg)
        return module
