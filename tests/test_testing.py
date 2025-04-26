from __future__ import annotations

from waku import WakuFactory, module
from waku.di import Scope, contextual, scoped
from waku.testing import override


class Service:
    def __init__(self, val: int) -> None:
        self.val = val

    def method(self) -> int:
        return self.val


class ServiceOverride(Service):
    def method(self) -> int:  # noqa: PLR6301
        return 42


async def test_override_helper() -> None:
    @module(
        providers=[
            contextual(int, scope=Scope.APP),
            scoped(Service),
        ]
    )
    class AppModule:
        pass

    val = 1
    application = WakuFactory(AppModule, context={int: val}).create()

    async with application:
        async with application.container() as request_container:
            original_service = await request_container.get(Service)
            assert isinstance(original_service, Service)
            assert original_service.method() == val

        with override(application.container, scoped(ServiceOverride, provided_type=Service)):
            async with application.container() as request_container:
                overrode_service = await request_container.get(Service)
                assert isinstance(overrode_service, ServiceOverride)
                assert overrode_service.method() == 42
