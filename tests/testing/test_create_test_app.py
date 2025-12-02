from typing import NewType, Protocol

from waku import module
from waku.di import contextual, singleton
from waku.di._activation import ActivationContext
from waku.extensions import OnModuleConfigure, OnModuleDestroy, OnModuleInit
from waku.modules import Module, ModuleMetadata
from waku.testing import create_test_app

TestValue = NewType('TestValue', str)


class IService(Protocol):
    def get_value(self) -> str: ...


class RealService:
    def get_value(self) -> str:  # noqa: PLR6301
        return 'real'


class FakeService:
    def get_value(self) -> str:  # noqa: PLR6301
        return 'fake'


async def test_create_test_app_with_providers() -> None:
    async with create_test_app(
        providers=[singleton(IService, FakeService)],
    ) as app:
        service = await app.container.get(IService)
        assert service.get_value() == 'fake'


async def test_create_test_app_with_context() -> None:
    expected_value = TestValue('test_value')

    async with create_test_app(
        providers=[contextual(TestValue)],
        context={TestValue: expected_value},
    ) as app:
        retrieved_value = await app.container.get(TestValue)
        assert retrieved_value == expected_value


async def test_create_test_app_with_extension() -> None:
    class TestExtension(OnModuleConfigure):
        def __init__(self) -> None:
            self.configured = False

        def on_module_configure(self, metadata: ModuleMetadata) -> None:
            self.configured = True
            metadata.providers.append(singleton(IService, FakeService))

    extension = TestExtension()

    async with create_test_app(extensions=[extension]) as app:
        assert extension.configured
        service = await app.container.get(IService)
        assert service.get_value() == 'fake'


async def test_create_test_app_with_imports() -> None:
    @module(providers=[singleton(IService, RealService)])
    class ImportedModule:
        pass

    async with create_test_app(imports=[ImportedModule]) as app:
        service = await app.container.get(IService)
        assert service.get_value() == 'real'


async def test_create_test_app_combined() -> None:
    class CountingExtension(OnModuleConfigure):
        def __init__(self) -> None:
            self.call_count = 0

        def on_module_configure(self, metadata: ModuleMetadata) -> None:  # noqa: ARG002
            self.call_count += 1

    extension = CountingExtension()

    async with create_test_app(
        providers=[singleton(IService, FakeService)],
        extensions=[extension],
        context={'env': 'test'},
    ) as app:
        assert extension.call_count == 1
        service = await app.container.get(IService)
        assert service.get_value() == 'fake'


async def test_create_test_app_lifecycle_hooks() -> None:
    lifecycle_events: list[str] = []

    class LifecycleExtension(OnModuleConfigure, OnModuleInit, OnModuleDestroy):
        def on_module_configure(self, metadata: ModuleMetadata) -> None:  # noqa: ARG002, PLR6301
            lifecycle_events.append('configure')

        async def on_module_init(self, module: Module) -> None:  # noqa: ARG002, PLR6301
            lifecycle_events.append('init')

        async def on_module_destroy(self, module: Module) -> None:  # noqa: ARG002, PLR6301
            lifecycle_events.append('destroy')

    extension = LifecycleExtension()

    async with create_test_app(extensions=[extension]):
        assert lifecycle_events == ['configure', 'init']

    assert lifecycle_events == ['configure', 'init', 'destroy']


async def test_create_test_app_with_base_module() -> None:
    @module(providers=[singleton(IService, RealService)])
    class BaseModule:
        pass

    async with create_test_app(
        base=BaseModule,
        providers=[singleton(IService, FakeService)],
    ) as app:
        service = await app.container.get(IService)
        assert service.get_value() == 'fake'


async def test_create_test_app_with_base_module_and_conditional_provider() -> None:
    @module(providers=[singleton(IService, RealService)])
    class BaseModule:
        pass

    def always_active(_ctx: ActivationContext) -> bool:
        return True

    conditional_provider = singleton(IService, FakeService, when=always_active)

    async with create_test_app(
        base=BaseModule,
        providers=[conditional_provider],
    ) as app:
        service = await app.container.get(IService)
        assert service.get_value() == 'fake'


async def test_create_test_app_base_module_without_override() -> None:
    @module(providers=[singleton(IService, RealService)])
    class BaseModule:
        pass

    async with create_test_app(base=BaseModule) as app:
        service = await app.container.get(IService)
        assert service.get_value() == 'real'


async def test_create_test_app_base_with_additional_imports() -> None:
    class IAnotherService(Protocol):
        def get_name(self) -> str: ...

    class AnotherService:
        def get_name(self) -> str:  # noqa: PLR6301
            return 'another'

    @module(providers=[singleton(IService, RealService)])
    class BaseModule:
        pass

    @module(providers=[singleton(IAnotherService, AnotherService)])
    class AdditionalModule:
        pass

    async with create_test_app(
        base=BaseModule,
        imports=[AdditionalModule],
        providers=[singleton(IService, FakeService)],
    ) as app:
        service = await app.container.get(IService)
        assert service.get_value() == 'fake'

        another = await app.container.get(IAnotherService)
        assert another.get_name() == 'another'


async def test_create_test_app_base_with_extensions() -> None:
    @module(providers=[singleton(IService, RealService)])
    class BaseModule:
        pass

    class TestExtension(OnModuleConfigure):
        def __init__(self) -> None:
            self.configured = False

        def on_module_configure(self, metadata: ModuleMetadata) -> None:  # noqa: ARG002
            self.configured = True

    extension = TestExtension()

    async with create_test_app(
        base=BaseModule,
        extensions=[extension],
        providers=[singleton(IService, FakeService)],
    ) as app:
        assert extension.configured
        service = await app.container.get(IService)
        assert service.get_value() == 'fake'
