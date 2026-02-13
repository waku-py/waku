from collections.abc import Sequence
from typing import Protocol

import pytest

from waku import WakuFactory
from waku.di import Scope, many, scoped

from tests.module_utils import create_basic_module


class IService(Protocol):
    pass


class ServiceA:
    pass


class ServiceB:
    pass


@pytest.mark.parametrize(
    ('implementations', 'expected_count'),
    [
        ([ServiceA], 1),
        ([ServiceA, ServiceB], 2),
    ],
)
async def test_many_provider_implementations(implementations: list[type], expected_count: int) -> None:
    AppModule = create_basic_module(
        providers=[many(IService, *implementations)],
        name='AppModule',
    )

    app = WakuFactory(AppModule).create()

    async with app, app.container() as container:
        services = await container.get(list[IService])
        assert len(services) == expected_count
        for i, impl_type in enumerate(implementations):
            assert isinstance(services[i], impl_type)


async def test_many_provider_as_sequence() -> None:
    AppModule = create_basic_module(
        providers=[many(IService, ServiceA, ServiceB)],
        name='AppModule',
    )

    app = WakuFactory(AppModule).create()

    async with app, app.container() as container:
        services_as_sequence = await container.get(Sequence[IService])
        assert len(services_as_sequence) == 2
        assert isinstance(services_as_sequence[0], ServiceA)
        assert isinstance(services_as_sequence[1], ServiceB)


@pytest.mark.parametrize(
    ('scope', 'cache', 'should_be_same'),
    [
        (Scope.APP, True, True),
        (Scope.REQUEST, True, True),
        (Scope.REQUEST, False, False),
    ],
)
async def test_many_provider_scope_and_cache(scope: Scope, cache: bool, should_be_same: bool) -> None:
    AppModule = create_basic_module(
        providers=[many(IService, ServiceA, scope=scope, cache=cache)],
        name='AppModule',
    )

    app = WakuFactory(AppModule).create()

    async with app, app.container() as container:
        services1 = await container.get(list[IService])
        services2 = await container.get(list[IService])

        if should_be_same:
            assert services1[0] is services2[0]
        else:
            assert services1[0] is not services2[0]


def test_many_provider_empty_implementations_error() -> None:
    class IEmpty(Protocol):
        pass

    with pytest.raises(ValueError, match='At least one implementation must be provided when collect=False'):
        many(IEmpty, collect=False)


async def test_many_provider_collect_only_without_implementations() -> None:
    class IEmpty(Protocol):
        pass

    AppModule = create_basic_module(
        providers=[many(IEmpty)],
        name='AppModule',
    )

    app = WakuFactory(AppModule).create()

    async with app, app.container() as container:
        services_seq = await container.get(Sequence[IEmpty])
        services_list = await container.get(list[IEmpty])
        assert list(services_seq) == []
        assert services_list == []


async def test_many_provider_with_factory_function() -> None:
    class IRuleStrategy(Protocol):
        def execute(self) -> str: ...

    class ConcreteRuleStrategy:
        def execute(self) -> str:  # noqa: PLR6301
            return 'executed'

    def rule_strategy_factory() -> IRuleStrategy:
        return ConcreteRuleStrategy()

    AppModule = create_basic_module(
        providers=[many(IRuleStrategy, rule_strategy_factory)],
        name='AppModule',
    )

    app = WakuFactory(AppModule).create()

    async with app, app.container() as container:
        strategies = await container.get(list[IRuleStrategy])
        assert len(strategies) == 1
        assert strategies[0].execute() == 'executed'


async def test_many_provider_with_mixed_classes_and_factories() -> None:
    class IProcessor(Protocol):
        def process(self) -> str: ...

    class ClassProcessor:
        def process(self) -> str:  # noqa: PLR6301
            return 'class'

    class FactoryProcessor:
        def process(self) -> str:  # noqa: PLR6301
            return 'factory'

    def processor_factory() -> IProcessor:
        return FactoryProcessor()

    AppModule = create_basic_module(
        providers=[many(IProcessor, ClassProcessor, processor_factory)],
        name='AppModule',
    )

    app = WakuFactory(AppModule).create()

    async with app, app.container() as container:
        processors = await container.get(list[IProcessor])
        assert len(processors) == 2
        assert processors[0].process() == 'class'
        assert processors[1].process() == 'factory'


async def test_many_provider_with_factory_with_dependencies() -> None:
    class IConfig(Protocol):
        def get_value(self) -> str: ...

    class Config:
        def get_value(self) -> str:  # noqa: PLR6301
            return 'config_value'

    class IHandler(Protocol):
        def handle(self) -> str: ...

    class Handler:
        def __init__(self, config: IConfig) -> None:
            self._config = config

        def handle(self) -> str:
            return f'handled_{self._config.get_value()}'

    def handler_factory(config: IConfig) -> IHandler:
        return Handler(config)

    AppModule = create_basic_module(
        providers=[
            scoped(IConfig, Config),
            many(IHandler, handler_factory),
        ],
        name='AppModule',
    )

    app = WakuFactory(AppModule).create()

    async with app, app.container() as container:
        handlers = await container.get(list[IHandler])
        assert len(handlers) == 1
        assert handlers[0].handle() == 'handled_config_value'


async def test_many_provider_with_multiple_factories() -> None:
    class IValidator(Protocol):
        def validate(self) -> str: ...

    class EmailValidator:
        def validate(self) -> str:  # noqa: PLR6301
            return 'email'

    class PhoneValidator:
        def validate(self) -> str:  # noqa: PLR6301
            return 'phone'

    def email_factory() -> EmailValidator:
        return EmailValidator()

    def phone_factory() -> PhoneValidator:
        return PhoneValidator()

    AppModule = create_basic_module(
        providers=[many(IValidator, email_factory, phone_factory)],
        name='AppModule',
    )

    app = WakuFactory(AppModule).create()

    async with app, app.container() as container:
        validators = await container.get(list[IValidator])
        assert len(validators) == 2
        assert validators[0].validate() == 'email'
        assert validators[1].validate() == 'phone'


async def test_many_provider_with_async_factory_function() -> None:
    class IAsyncService(Protocol):
        async def execute(self) -> str: ...

    class AsyncService:
        async def execute(self) -> str:  # noqa: PLR6301
            return 'async_executed'

    async def async_service_factory() -> IAsyncService:  # noqa: RUF029
        return AsyncService()

    AppModule = create_basic_module(
        providers=[many(IAsyncService, async_service_factory)],
        name='AppModule',
    )

    app = WakuFactory(AppModule).create()

    async with app, app.container() as container:
        services = await container.get(list[IAsyncService])
        assert len(services) == 1
        result = await services[0].execute()
        assert result == 'async_executed'
