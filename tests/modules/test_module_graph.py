from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest

from waku import DynamicModule, WakuFactory, module
from waku.exceptions import ImproperlyConfiguredError

from tests.module_utils import create_basic_module

if TYPE_CHECKING:
    from waku.modules import HasModuleMetadata


def test_cyclic_module_imports_raise_naming_the_cycle() -> None:
    ModuleA = create_basic_module(name='ModuleA')
    ModuleB = create_basic_module(imports=[ModuleA], name='ModuleB')
    cast('HasModuleMetadata', cast('object', ModuleA)).__module_metadata__.imports.append(ModuleB)

    with pytest.raises(ImproperlyConfiguredError, match='ModuleA -> ModuleB -> ModuleA'):
        WakuFactory(ModuleA).create()


def test_self_importing_module_raises() -> None:
    SelfModule = create_basic_module(name='SelfModule')
    cast('HasModuleMetadata', cast('object', SelfModule)).__module_metadata__.imports.append(SelfModule)

    with pytest.raises(ImproperlyConfiguredError, match='SelfModule -> SelfModule'):
        WakuFactory(SelfModule).create()


def test_two_dynamic_modules_for_one_parent_fail_loud() -> None:
    @module()
    class ConfigParent:
        pass

    dyn_a = DynamicModule(parent_module=ConfigParent)
    dyn_b = DynamicModule(parent_module=ConfigParent)
    AppModule = create_basic_module(imports=[dyn_a, dyn_b], name='App')

    with pytest.raises(ImproperlyConfiguredError, match='ConfigParent'):
        WakuFactory(AppModule).create()


def test_single_dynamic_module_registration_still_resolves() -> None:
    @module()
    class ConfigParent:
        pass

    dyn = DynamicModule(parent_module=ConfigParent)
    AppModule = create_basic_module(imports=[dyn], name='App')

    application = WakuFactory(AppModule).create()

    assert application.registry.get(ConfigParent).id == dyn.id
