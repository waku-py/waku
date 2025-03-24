from waku import module
from waku.di import Injected, inject, Scoped


class Service:
    def great(self, name: str) -> None:
        print(f'Hello, {name}!')


@module(
    providers=[Scoped(Service)],
)
class SomeModule:
    pass


@inject
async def some_handler(service: Injected[Service]) -> None:
    service.great('waku')
