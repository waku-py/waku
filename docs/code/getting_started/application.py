from waku import WakuApplication, WakuFactory, module

from app.modules.greetings.module import GreetingModule
from app.modules.users.module import UserModule
from app.settings import ConfigModule


@module(
    # Import all top-level modules
    imports=[
        ConfigModule.register(env='dev'),
        GreetingModule,
        UserModule,
    ],
)
class AppModule:
    pass


def bootstrap_application() -> WakuApplication:
    return WakuFactory(AppModule).create()
