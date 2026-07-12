from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from typing_extensions import override

from waku.backends.memory import MemoryBackend
from waku.exceptions import ImproperlyConfiguredError
from waku.messages import IEvent
from waku.messaging import MessagingConfig, MessagingExtension, MessagingModule
from waku.messaging.durability import IDeadLetterStore
from waku.messaging.errors import DeadLetterDestinationKind, DeadLetterEntry, ErrorPolicy
from waku.messaging.handler import EventHandler
from waku.testing import create_test_app

if TYPE_CHECKING:
    from typing import ClassVar


@dataclass(frozen=True)
class _BoomEvent(IEvent):
    value: int


def _entry() -> DeadLetterEntry:
    return DeadLetterEntry.from_failure(
        message_type='tests.Boom',
        payload={'value': 1},
        destination='local://boom',
        destination_kind=DeadLetterDestinationKind.ENDPOINT,
        correlation_id=str(uuid4()),
        causation_id=str(uuid4()),
        exc=RuntimeError('boom'),
        attempt=1,
    )


class _DeadLetteringHandler(EventHandler[_BoomEvent]):
    error_policies: ClassVar = (ErrorPolicy.on_any_exception().move_to_dead_letter(),)

    @override
    async def handle(self, event: _BoomEvent, /) -> None:  # pragma: no cover
        msg = 'always fails'
        raise RuntimeError(msg)


async def test_no_backend_resolves_the_discarding_store_that_persists_nothing() -> None:
    async with (
        create_test_app(imports=[MessagingModule.register(MessagingConfig())]) as app,
        app.container() as scope,
    ):
        store = await scope.get(IDeadLetterStore)
        entry = _entry()

        await store.save(entry)

        assert await store.fetch() == ()
        with pytest.raises(KeyError):
            await store.fetch_one(entry.id)


async def test_backend_present_persists_dead_letters_even_without_dead_letter_config() -> None:
    async with (
        create_test_app(
            imports=[MessagingModule.register(MessagingConfig()), MemoryBackend.register()],
        ) as app,
        app.container() as scope,
    ):
        store = await scope.get(IDeadLetterStore)
        entry = _entry()

        await store.save(entry)

        assert (await store.fetch_one(entry.id)).id == entry.id


async def test_dead_letter_policy_without_config_boots_when_a_backend_provides_the_store() -> None:
    async with create_test_app(
        imports=[MessagingModule.register(MessagingConfig()), MemoryBackend.register()],
        extensions=[MessagingExtension().bind(_DeadLetteringHandler)],
    ):
        pass


async def test_dead_letter_policy_without_config_and_without_backend_still_raises() -> None:
    with pytest.raises(ImproperlyConfiguredError, match='require dead_letter'):
        async with create_test_app(
            imports=[MessagingModule.register(MessagingConfig())],
            extensions=[MessagingExtension().bind(_DeadLetteringHandler)],
        ):
            pass  # pragma: no cover
