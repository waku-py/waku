from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from typing_extensions import override

from waku.messages import IEvent
from waku.messaging import IMessageBus, MessagingModule
from waku.messaging._internal.dispatch import IEndpointDispatch
from waku.messaging._internal.envelope_factory import EnvelopeFactory
from waku.messaging.context import message_context_scope
from waku.messaging.endpoints.base import Endpoint
from waku.testing import create_test_app

from tests.messaging.helpers import make_envelope

if TYPE_CHECKING:
    from pytest_mock import MockerFixture

    from waku.di import AsyncContainer
    from waku.messaging.contracts.envelope import MessageEnvelope


@dataclass(frozen=True, kw_only=True)
class _SomethingHappened(IEvent):
    payload: str


@dataclass(frozen=True, kw_only=True)
class _CascadedEffect(IEvent):
    payload: str


class _RecordingEndpoint(Endpoint):
    def __init__(self, uri: str) -> None:
        super().__init__(uri=uri)
        self.dispatched: list[MessageEnvelope[Any]] = []

    @override
    async def dispatch(self, envelope: MessageEnvelope[Any], scope: AsyncContainer) -> None:
        self.dispatched.append(envelope)


async def test_endpoint_dispatch_resolves_to_the_same_scoped_instance_as_the_bus() -> None:
    async with (
        create_test_app(imports=[MessagingModule.register()]) as app,
        app.container() as container,
    ):
        bus = await container.get(IMessageBus)
        dispatch = await container.get(IEndpointDispatch)

        assert dispatch is bus


async def test_dispatch_to_serves_each_endpoint_exactly_once_with_one_context_propagated_envelope() -> None:
    async with (
        create_test_app(imports=[MessagingModule.register()]) as app,
        app.container() as container,
    ):
        dispatch = await container.get(IEndpointDispatch)
        endpoint_a = _RecordingEndpoint('local://a')
        endpoint_b = _RecordingEndpoint('local://b')
        origin = make_envelope(_SomethingHappened(payload='origin'))

        with message_context_scope(origin):
            await dispatch.dispatch_to(_CascadedEffect(payload='effect'), [endpoint_a, endpoint_b])

        assert [type(envelope.payload) for envelope in endpoint_a.dispatched] == [_CascadedEffect]
        assert [type(envelope.payload) for envelope in endpoint_b.dispatched] == [_CascadedEffect]
        # ONE envelope per cascade, shared across destinations, with the originating context applied.
        envelope = endpoint_a.dispatched[0]
        assert endpoint_b.dispatched[0] is envelope
        assert envelope.causation_id == str(origin.message_id)
        assert envelope.correlation_id == origin.correlation_id


async def test_dispatch_to_with_no_endpoints_creates_no_envelope(mocker: MockerFixture) -> None:
    async with (
        create_test_app(imports=[MessagingModule.register()]) as app,
        app.container() as container,
    ):
        dispatch = await container.get(IEndpointDispatch)
        create = mocker.patch.object(EnvelopeFactory, 'create')

        await dispatch.dispatch_to(_CascadedEffect(payload='effect'), [])

        create.assert_not_called()
