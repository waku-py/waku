from typing import Protocol

from tests.data import UserService
from tests.module_utils import create_basic_module
from waku import WakuFactory
from waku.di import object_, scoped


async def test_object_provider_instance_identity() -> None:
    """Object provider should maintain instance identity and values."""
    instance = UserService(user_id=1337)

    AppModule = create_basic_module(
        providers=[object_(instance, provided_type=UserService)],
        name='AppModule',
    )

    app = WakuFactory(AppModule).create()

    async with app, app.container() as container:
        result = await container.get(UserService)
        assert result is instance
        assert result.user_id == 1337


async def test_provider_with_base_class_type() -> None:
    """Provider should support providing implementation through base class type."""

    class BaseService(Protocol):
        pass

    class ConcreteService(BaseService):
        pass

    AppModule = create_basic_module(
        providers=[scoped(ConcreteService, provided_type=BaseService)],
        name='AppModule',
    )

    app = WakuFactory(AppModule).create()

    async with app, app.container() as container:
        result = await container.get(BaseService)
        assert isinstance(result, ConcreteService)
