from typing import Protocol

from waku import WakuFactory
from waku.di import scoped

from tests.module_utils import create_basic_module


async def test_provider_with_base_class_type() -> None:
    class BaseService(Protocol):
        pass

    class ConcreteService(BaseService):
        pass

    AppModule = create_basic_module(
        providers=[scoped(BaseService, ConcreteService)],
        name='AppModule',
    )

    app = WakuFactory(AppModule).create()

    async with app, app.container() as container:
        result = await container.get(BaseService)
        assert isinstance(result, ConcreteService)
