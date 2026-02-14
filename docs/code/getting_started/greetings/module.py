from waku import module
from waku.di import singleton

from app.modules.greetings.services import GreetingService


@module(
    providers=[singleton(GreetingService)],
    exports=[GreetingService],
)
class GreetingModule:
    pass
