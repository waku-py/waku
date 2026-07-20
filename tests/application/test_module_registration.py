from waku import WakuFactory
from waku.di import scoped, singleton

from tests.data import A, C
from tests.module_utils import create_basic_module


async def test_module_registration_with_dependencies() -> None:
    ModuleA = create_basic_module(
        providers=[scoped(A)],
        exports=[A],
        name='ModuleA',
    )
    ModuleB = create_basic_module(
        providers=[singleton(C)],
        imports=[ModuleA],
        name='ModuleB',
    )
    AppModule = create_basic_module(
        imports=[ModuleA, ModuleB],
        name='AppModule',
    )

    application = WakuFactory(AppModule).create()

    async with application, application.container() as request_container:
        await request_container.get(A)
        await application.container.get(C)
