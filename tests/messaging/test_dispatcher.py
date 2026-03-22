from __future__ import annotations

from dataclasses import dataclass

from typing_extensions import override

from waku.messaging import (
    EventHandler,
    IEvent,
    MessagingConfig,
    MessagingExtension,
    MessagingModule,
)
from waku.messaging.dispatcher import MessageDispatcher
from waku.testing import create_test_app


@dataclass(frozen=True)
class _Evt(IEvent):
    value: str


class TestPublishEventExcluding:
    @staticmethod
    async def test_excludes_specified_handlers() -> None:
        called: list[str] = []

        class HandlerA(EventHandler[_Evt]):
            @override
            async def handle(self, event: _Evt, /) -> None:  # pragma: no cover
                called.append(f'A:{event.value}')

        class HandlerB(EventHandler[_Evt]):
            @override
            async def handle(self, event: _Evt, /) -> None:
                called.append(f'B:{event.value}')

        async with (
            create_test_app(
                imports=[MessagingModule.register(MessagingConfig())],
                extensions=[MessagingExtension().bind_event(_Evt, [HandlerA, HandlerB])],
            ) as app,
            app.container() as container,
        ):
            dispatcher = await container.get(MessageDispatcher)
            await dispatcher.publish_event_excluding(_Evt(value='x'), exclude=frozenset({HandlerA}))

        assert called == ['B:x']

    @staticmethod
    async def test_excludes_none_when_empty_set() -> None:
        called: list[str] = []

        class HandlerA(EventHandler[_Evt]):
            @override
            async def handle(self, event: _Evt, /) -> None:
                called.append(f'A:{event.value}')

        class HandlerB(EventHandler[_Evt]):
            @override
            async def handle(self, event: _Evt, /) -> None:
                called.append(f'B:{event.value}')

        async with (
            create_test_app(
                imports=[MessagingModule.register(MessagingConfig())],
                extensions=[MessagingExtension().bind_event(_Evt, [HandlerA, HandlerB])],
            ) as app,
            app.container() as container,
        ):
            dispatcher = await container.get(MessageDispatcher)
            await dispatcher.publish_event_excluding(_Evt(value='y'), exclude=frozenset())

        assert called == ['A:y', 'B:y']


class TestPublishEventOnly:
    @staticmethod
    async def test_runs_only_specified_handlers() -> None:
        called: list[str] = []

        class HandlerA(EventHandler[_Evt]):
            @override
            async def handle(self, event: _Evt, /) -> None:  # pragma: no cover
                called.append(f'A:{event.value}')

        class HandlerB(EventHandler[_Evt]):
            @override
            async def handle(self, event: _Evt, /) -> None:
                called.append(f'B:{event.value}')

        async with (
            create_test_app(
                imports=[MessagingModule.register(MessagingConfig())],
                extensions=[MessagingExtension().bind_event(_Evt, [HandlerA, HandlerB])],
            ) as app,
            app.container() as container,
        ):
            dispatcher = await container.get(MessageDispatcher)
            await dispatcher.publish_event_only(_Evt(value='z'), only=frozenset({HandlerB}))

        assert called == ['B:z']
