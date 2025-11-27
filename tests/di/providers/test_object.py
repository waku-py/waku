from waku import WakuFactory
from waku.di import object_

from tests.data import UserService
from tests.module_utils import create_basic_module


async def test_object_provider_instance_identity() -> None:
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
