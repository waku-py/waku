from waku import module
from waku.di import Singleton

from app.modules.greetings.services import GreetingService


@module(
    providers=[Singleton(GreetingService)],
    exports=[GreetingService],
)
class GreetingModule:
    pass
