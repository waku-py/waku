from waku import WakuApplication, WakuFactory, module
from waku.di.contrib.aioinject import AioinjectDependencyProvider

from app.settings import ConfigModule
from app.greetings.module import GreetingModule
from app.users.module import UserModule


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
    return WakuFactory.create(
        AppModule,
        dependency_provider=AioinjectDependencyProvider(),
    )
