from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

import pytest
from typing_extensions import override

from waku.di import is_registered, object_, scoped
from waku.exceptions import ImproperlyConfiguredError
from waku.messages import IEvent
from waku.messaging import (
    CallNext,
    EndpointDefaults,
    EndpointMode,
    EventHandler,
    HandlerMap,
    IMessageBus,
    InboxConfig,
    IPipelineBehavior,
    IRequest,
    MessageT,
    MessagingConfig,
    MessagingExtension,
    MessagingModule,
    OutboxConfig,
    RequestHandler,
    ResponseT,
    TransactionalBehavior,
)
from waku.messaging._internal.maintenance import DurabilityMaintenanceLifecycleExtension
from waku.messaging.config import DeadLetterConfig
from waku.messaging.context import get_message_context
from waku.messaging.durability import IDeadLetterStore, IDurabilityStore, IInboxStore, IOutboxStore
from waku.messaging.errors.policy import ErrorPolicy
from waku.messaging.errors.replay import ReplayExecutor
from waku.messaging.exceptions import (
    HandlerNotFoundError,
    MultipleHandlersRegisteredError,
    NoRouteError,
)
from waku.messaging.pipeline._internal.plan import BehaviorPlan
from waku.messaging.router import external_endpoint, listen, local_queue
from waku.messaging.sequence import ISequenceAllocator
from waku.testing import create_test_app
from waku.uow import IUnitOfWork

from tests.messaging.helpers import (
    RecordingAllocator,
    RecordingDeadLetterStore,
    RecordingDurabilityStore,
    RecordingTransport,
    RecordingUoW,
    order_id_partition,
)
from tests.messaging.inbox.fake_store import FakeInboxStore
from tests.messaging.outbox.fake_store import RecordingOutboxStore


def _durability(
    *,
    unit_of_work: IUnitOfWork,
    outbox: IOutboxStore | None = None,
    inbox: IInboxStore | None = None,
    dead_letters: IDeadLetterStore | None = None,
) -> RecordingDurabilityStore:
    return RecordingDurabilityStore(
        unit_of_work=unit_of_work,
        outbox=outbox or RecordingOutboxStore(),
        inbox=inbox or FakeInboxStore(),
        dead_letters=dead_letters or RecordingDeadLetterStore(),
    )


@dataclass(frozen=True, kw_only=True)
class _Result:
    value: str


@dataclass(frozen=True, kw_only=True)
class _Command(IRequest[_Result]):
    name: str


class _CommandHandler(RequestHandler[_Command, _Result]):
    @override
    async def handle(self, request: _Command, /) -> _Result:
        return _Result(value=request.name)


class _UnregisteredCommand(IRequest[None]):
    pass


@dataclass(frozen=True)
class _SomeEvent(IEvent):
    pass


async def test_invoke_returns_handler_result() -> None:
    async with (
        create_test_app(
            imports=[MessagingModule.register(MessagingConfig())],
            extensions=[MessagingExtension().bind(_CommandHandler)],
        ) as app,
        app.container() as container,
    ):
        bus = await container.get(IMessageBus)
        result = await bus.invoke(_Command(name='hello'))
        assert result == _Result(value='hello')


async def test_multiple_request_handlers_rejected_at_startup() -> None:
    class _AnotherCommandHandler(RequestHandler[_Command, _Result]):
        @override
        async def handle(self, request: _Command, /) -> _Result:  # pragma: no cover
            return _Result(value='other')

    with pytest.raises(MultipleHandlersRegisteredError, match='_Command'):
        async with create_test_app(
            imports=[MessagingModule.register(MessagingConfig())],
            extensions=[MessagingExtension().bind(_CommandHandler).bind(_AnotherCommandHandler)],
        ):
            pass  # pragma: no cover


async def test_invoke_raises_for_unregistered_request() -> None:
    async with (
        create_test_app(
            imports=[MessagingModule.register(MessagingConfig())],
        ) as app,
        app.container() as container,
    ):
        bus = await container.get(IMessageBus)
        with pytest.raises(HandlerNotFoundError, match='No handler registered for _UnregisteredCommand'):
            await bus.invoke(_UnregisteredCommand())


async def test_publish_runs_global_behaviors_per_handler() -> None:
    called: list[str] = []

    class TrackingBehavior(IPipelineBehavior[MessageT, ResponseT]):
        @override
        async def handle(self, message: MessageT, /, call_next: CallNext[ResponseT]) -> ResponseT:
            called.append(f'behavior:{type(message).__name__}')
            return await call_next()

    class HandlerA(EventHandler[_SomeEvent]):
        @override
        async def handle(self, event: _SomeEvent, /) -> None:
            called.append('handler_a')

    class HandlerB(EventHandler[_SomeEvent]):
        @override
        async def handle(self, event: _SomeEvent, /) -> None:
            called.append('handler_b')

    async with (
        create_test_app(
            imports=[
                MessagingModule.register(MessagingConfig(global_pipeline_behaviors=[TrackingBehavior])),
            ],
            extensions=[MessagingExtension().bind(HandlerA, HandlerB)],
        ) as app,
        app.container() as container,
    ):
        bus = await container.get(IMessageBus)
        await bus.publish(_SomeEvent())

    assert called.count('behavior:_SomeEvent') == 2
    assert called.count('handler_a') == 1
    assert called.count('handler_b') == 1
    assert len(called) == 4


async def test_publish_runs_per_handler_behavior_for_bound_event() -> None:
    called: list[str] = []

    class ScopedBehavior(IPipelineBehavior[MessageT, ResponseT]):
        @override
        async def handle(self, message: MessageT, /, call_next: CallNext[ResponseT]) -> ResponseT:
            called.append('scoped_behavior')
            return await call_next()

    class Handler(EventHandler[_SomeEvent]):
        behaviors = (ScopedBehavior,)

        @override
        async def handle(self, event: _SomeEvent, /) -> None:
            called.append('handler')

    async with (
        create_test_app(
            imports=[MessagingModule.register(MessagingConfig())],
            extensions=[MessagingExtension().bind(Handler)],
        ) as app,
        app.container() as container,
    ):
        bus = await container.get(IMessageBus)
        await bus.publish(_SomeEvent())

    assert called == ['scoped_behavior', 'handler']


async def test_publish_runs_global_then_per_handler_behaviors() -> None:
    called: list[str] = []

    class GlobalBehavior(IPipelineBehavior[MessageT, ResponseT]):
        @override
        async def handle(self, message: MessageT, /, call_next: CallNext[ResponseT]) -> ResponseT:
            called.append('global')
            return await call_next()

    class ScopedBehavior(IPipelineBehavior[MessageT, ResponseT]):
        @override
        async def handle(self, message: MessageT, /, call_next: CallNext[ResponseT]) -> ResponseT:
            called.append('scoped')
            return await call_next()

    class Handler(EventHandler[_SomeEvent]):
        behaviors = (ScopedBehavior,)

        @override
        async def handle(self, event: _SomeEvent, /) -> None:
            called.append('handler')

    async with (
        create_test_app(
            imports=[MessagingModule.register(MessagingConfig(global_pipeline_behaviors=[GlobalBehavior]))],
            extensions=[MessagingExtension().bind(Handler)],
        ) as app,
        app.container() as container,
    ):
        bus = await container.get(IMessageBus)
        await bus.publish(_SomeEvent())

    assert called == ['global', 'scoped', 'handler']


async def test_publish_per_handler_behavior_does_not_run_for_other_event() -> None:
    called: list[str] = []

    @dataclass(frozen=True)
    class OtherEvent(IEvent):
        pass

    class ScopedBehavior(IPipelineBehavior[MessageT, ResponseT]):
        @override
        async def handle(self, message: MessageT, /, call_next: CallNext[ResponseT]) -> ResponseT:  # pragma: no cover
            called.append('scoped_behavior')
            return await call_next()

    class SomeEventHandler(EventHandler[_SomeEvent]):
        behaviors = (ScopedBehavior,)

        @override
        async def handle(self, event: _SomeEvent, /) -> None:  # pragma: no cover
            called.append('some_handler')

    class OtherEventHandler(EventHandler[OtherEvent]):
        @override
        async def handle(self, event: OtherEvent, /) -> None:
            called.append('other_handler')

    async with (
        create_test_app(
            imports=[MessagingModule.register(MessagingConfig())],
            extensions=[
                MessagingExtension().bind(SomeEventHandler).bind(OtherEventHandler),
            ],
        ) as app,
        app.container() as container,
    ):
        bus = await container.get(IMessageBus)
        await bus.publish(OtherEvent())

    assert called == ['other_handler']


async def test_publish_without_handlers_does_nothing() -> None:
    async with (
        create_test_app(
            imports=[MessagingModule.register(MessagingConfig())],
        ) as app,
        app.container() as container,
    ):
        bus = await container.get(IMessageBus)
        await bus.publish(_SomeEvent())


async def test_send_without_route_raises_no_route_error() -> None:
    async with (
        create_test_app(
            imports=[MessagingModule.register(MessagingConfig())],
        ) as app,
        app.container() as container,
    ):
        bus = await container.get(IMessageBus)
        with pytest.raises(NoRouteError, match=r"no endpoint routes '_UnregisteredCommand'") as exc_info:
            await bus.send(_UnregisteredCommand())
        message = str(exc_info.value)
        assert 'invoke()' in message
        assert 'publish()' in message
        assert 'route(' in message


async def test_publish_event_handler_failure_does_not_block_other_handlers() -> None:
    called: list[str] = []

    class FailingHandler(EventHandler[_SomeEvent]):
        @override
        async def handle(self, event: _SomeEvent, /) -> None:
            called.append('failing')
            msg = 'handler error'
            raise RuntimeError(msg)

    class SucceedingHandler(EventHandler[_SomeEvent]):
        @override
        async def handle(self, event: _SomeEvent, /) -> None:
            called.append('succeeding')

    async with (
        create_test_app(
            imports=[MessagingModule.register(MessagingConfig())],
            extensions=[MessagingExtension().bind(FailingHandler, SucceedingHandler)],
        ) as app,
        app.container() as container,
    ):
        bus = await container.get(IMessageBus)
        await bus.publish(_SomeEvent())

    assert 'succeeding' in called
    assert 'failing' in called
    assert len(called) == 2


async def test_send_dispatches_through_default_endpoint() -> None:
    called: list[str] = []

    @dataclass(frozen=True, kw_only=True)
    class _FireAndForgetCommand(IRequest[None]):
        name: str

    class _FireAndForgetHandler(RequestHandler[_FireAndForgetCommand, None]):
        @override
        async def handle(self, request: _FireAndForgetCommand, /) -> None:
            called.append(request.name)

    async with (
        create_test_app(
            imports=[MessagingModule.register(MessagingConfig())],
            extensions=[MessagingExtension().bind(_FireAndForgetHandler)],
        ) as app,
        app.container() as container,
    ):
        bus = await container.get(IMessageBus)
        await bus.send(_FireAndForgetCommand(name='queued'))

    assert called == ['queued']


async def test_publish_propagates_correlation_context_through_queue() -> None:
    command_context: dict[str, object] = {}
    event_context: dict[str, object] = {}

    class PublishingCommandHandler(RequestHandler[_Command, _Result]):
        def __init__(self, bus: IMessageBus) -> None:
            self._bus = bus

        @override
        async def handle(self, request: _Command, /) -> _Result:
            ctx = get_message_context()
            command_context['correlation_id'] = ctx.correlation_id
            command_context['message_id'] = ctx.message_id
            await self._bus.publish(_SomeEvent())
            return _Result(value=request.name)

    class ContextCapturingHandler(EventHandler[_SomeEvent]):
        @override
        async def handle(self, event: _SomeEvent, /) -> None:
            ctx = get_message_context()
            event_context['correlation_id'] = ctx.correlation_id
            event_context['causation_id'] = ctx.causation_id

    async with (
        create_test_app(
            imports=[MessagingModule.register(MessagingConfig())],
            extensions=[
                MessagingExtension().bind(PublishingCommandHandler).bind(ContextCapturingHandler),
            ],
        ) as app,
        app.container() as container,
    ):
        bus = await container.get(IMessageBus)
        await bus.invoke(_Command(name='test'))

    assert event_context['correlation_id'] == command_context['correlation_id']
    assert event_context['causation_id'] == str(command_context['message_id'])


class TestMessagingConfigValidation:  # noqa: PLR0904 -- cohesive startup validation matrix
    @staticmethod
    def test_external_endpoint_without_outbox_raises() -> None:
        config = MessagingConfig(
            endpoints=[external_endpoint('ext://bus')],
        )
        with pytest.raises(ImproperlyConfiguredError, match='external_endpoint requires outbox'):
            MessagingModule.register(config)

    @staticmethod
    async def test_dead_letter_policy_without_dead_letter_store_raises() -> None:
        config = MessagingConfig(
            endpoint_defaults=EndpointDefaults(error_policies=(ErrorPolicy.on_any_exception().move_to_dead_letter(),)),
        )
        with pytest.raises(ImproperlyConfiguredError, match='dead_letter'):
            async with create_test_app(imports=[MessagingModule.register(config)]):
                pass  # pragma: no cover

    @staticmethod
    async def test_dead_letter_escalation_without_dead_letter_store_raises() -> None:
        config = MessagingConfig(
            endpoint_defaults=EndpointDefaults(
                error_policies=(ErrorPolicy.on_any_exception().retry(max_attempts=3).then_move_to_dead_letter(),),
            ),
        )
        with pytest.raises(ImproperlyConfiguredError, match='dead_letter'):
            async with create_test_app(imports=[MessagingModule.register(config)]):
                pass  # pragma: no cover

    @staticmethod
    async def test_dead_letter_store_without_uow_raises_at_startup() -> None:
        config = MessagingConfig(
            dead_letter=DeadLetterConfig(),
        )
        dead_letters = RecordingDeadLetterStore()
        with pytest.raises(ImproperlyConfiguredError, match='IUnitOfWork'):
            async with create_test_app(
                imports=[MessagingModule.register(config)],
                providers=[
                    object_(dead_letters, provided_type=IDeadLetterStore),
                    object_(
                        _durability(unit_of_work=RecordingUoW(), dead_letters=dead_letters),
                        provided_type=IDurabilityStore,
                    ),
                ],
            ):
                pass  # pragma: no cover

    @staticmethod
    async def test_durable_outbox_without_explicit_transactional_behavior_boots() -> None:
        outbox = RecordingOutboxStore()
        unit_of_work = RecordingUoW()
        config = MessagingConfig(
            outbox=OutboxConfig(),
            transports={'test': RecordingTransport},
        )
        async with create_test_app(
            imports=[MessagingModule.register(config)],
            extensions=[MessagingExtension().bind(_CommandHandler)],
            providers=[
                object_(unit_of_work, provided_type=IUnitOfWork),
                object_(outbox, provided_type=IOutboxStore),
                object_(_durability(unit_of_work=unit_of_work, outbox=outbox), provided_type=IDurabilityStore),
            ],
        ) as app:
            plan = await app.container.get(BehaviorPlan)
            registry = await app.container.get(HandlerMap)
            handler_types = registry.handler_types()
            assert handler_types
            assert all(TransactionalBehavior in plan.for_handler(handler_type) for handler_type in handler_types)

    @staticmethod
    def test_dead_letter_config_defaults() -> None:
        config = DeadLetterConfig()
        assert config.auto_replay_enabled is False
        assert config.max_replay_count == 3
        assert config.retention is None

    @staticmethod
    async def test_dead_letter_config_registers_store_and_replay_executor() -> None:
        config = MessagingConfig(dead_letter=DeadLetterConfig())
        dead_letters = RecordingDeadLetterStore()
        unit_of_work = RecordingUoW()
        async with (
            create_test_app(
                imports=[MessagingModule.register(config)],
                providers=[
                    object_(unit_of_work, provided_type=IUnitOfWork),
                    object_(dead_letters, provided_type=IDeadLetterStore),
                    object_(
                        _durability(unit_of_work=unit_of_work, dead_letters=dead_letters),
                        provided_type=IDurabilityStore,
                    ),
                ],
            ) as app,
            app.container() as scope,
        ):
            assert await is_registered(scope, IDeadLetterStore)
            assert await is_registered(scope, ReplayExecutor)

    @staticmethod
    async def test_backendless_config_resolves_no_persistence_capability() -> None:
        async with (
            create_test_app(imports=[MessagingModule.register(MessagingConfig())]) as app,
            app.container() as scope,
        ):
            assert not await is_registered(scope, IDeadLetterStore)
            assert not await is_registered(scope, IUnitOfWork)
            assert not await is_registered(scope, ReplayExecutor)

    @staticmethod
    @pytest.mark.parametrize('mismatch', ['unit_of_work', 'outbox', 'inbox', 'dead_letters'])
    async def test_durability_capability_rejects_mismatched_scoped_identity(mismatch: str) -> None:
        unit_of_work = RecordingUoW()
        outbox = RecordingOutboxStore()
        inbox = FakeInboxStore()
        dead_letters = RecordingDeadLetterStore()
        durability = RecordingDurabilityStore(
            unit_of_work=RecordingUoW() if mismatch == 'unit_of_work' else unit_of_work,
            outbox=RecordingOutboxStore() if mismatch == 'outbox' else outbox,
            inbox=FakeInboxStore() if mismatch == 'inbox' else inbox,
            dead_letters=RecordingDeadLetterStore() if mismatch == 'dead_letters' else dead_letters,
        )
        config = MessagingConfig(
            outbox=OutboxConfig(),
            inbox=InboxConfig(),
            dead_letter=DeadLetterConfig(),
        )

        with pytest.raises(ImproperlyConfiguredError, match=mismatch):
            async with create_test_app(
                imports=[MessagingModule.register(config)],
                providers=[
                    object_(unit_of_work, provided_type=IUnitOfWork),
                    object_(outbox, provided_type=IOutboxStore),
                    object_(inbox, provided_type=IInboxStore),
                    object_(dead_letters, provided_type=IDeadLetterStore),
                    object_(durability, provided_type=IDurabilityStore),
                    object_(RecordingAllocator(), provided_type=ISequenceAllocator),
                ],
            ):
                pass  # pragma: no cover

    @staticmethod
    async def test_dead_letter_config_rejects_unrelated_store_and_uow() -> None:
        config = MessagingConfig(dead_letter=DeadLetterConfig())

        with pytest.raises(ImproperlyConfiguredError, match='IDurabilityStore'):
            async with create_test_app(
                imports=[MessagingModule.register(config)],
                providers=[
                    object_(RecordingUoW(), provided_type=IUnitOfWork),
                    scoped(IDeadLetterStore, RecordingDeadLetterStore),
                ],
            ):
                pass  # pragma: no cover

    @staticmethod
    async def test_partition_by_without_allocator_raises_at_startup() -> None:
        outbox = RecordingOutboxStore()
        unit_of_work = RecordingUoW()
        config = MessagingConfig(
            endpoints=[external_endpoint('ext://orders', partition_by=order_id_partition)],
            outbox=OutboxConfig(),
            transports={'ext': RecordingTransport},
            global_pipeline_behaviors=[TransactionalBehavior],
        )
        with pytest.raises(ImproperlyConfiguredError, match='ISequenceAllocator'):
            async with create_test_app(
                imports=[MessagingModule.register(config)],
                providers=[
                    object_(unit_of_work, provided_type=IUnitOfWork),
                    object_(outbox, provided_type=IOutboxStore),
                    object_(_durability(unit_of_work=unit_of_work, outbox=outbox), provided_type=IDurabilityStore),
                ],
            ):
                pass  # pragma: no cover

    @staticmethod
    def test_partition_by_on_buffered_local_queue_raises() -> None:
        config = MessagingConfig(
            endpoints=[local_queue('q://orders', mode=EndpointMode.BUFFERED, partition_by=order_id_partition)],
        )
        with pytest.raises(ImproperlyConfiguredError, match='partition_by'):
            MessagingModule.register(config)

    @staticmethod
    def test_partition_by_on_inline_local_queue_raises() -> None:
        config = MessagingConfig(
            endpoints=[local_queue('q://orders', mode=EndpointMode.INLINE, partition_by=order_id_partition)],
        )
        with pytest.raises(ImproperlyConfiguredError, match='partition_by'):
            MessagingModule.register(config)

    @staticmethod
    async def test_partition_by_on_durable_local_queue_does_not_raise() -> None:
        inbox = FakeInboxStore()
        unit_of_work = RecordingUoW()
        config = MessagingConfig(
            endpoints=[local_queue('q://orders', mode=EndpointMode.DURABLE, partition_by=order_id_partition)],
            inbox=InboxConfig(),
            global_pipeline_behaviors=[TransactionalBehavior],
        )
        async with create_test_app(
            imports=[MessagingModule.register(config)],
            providers=[
                object_(unit_of_work, provided_type=IUnitOfWork),
                object_(RecordingAllocator(), provided_type=ISequenceAllocator),
                object_(inbox, provided_type=IInboxStore),
                object_(_durability(unit_of_work=unit_of_work, inbox=inbox), provided_type=IDurabilityStore),
            ],
        ):
            pass

    @staticmethod
    async def test_partition_by_on_broker_endpoint_does_not_raise_local_reject() -> None:
        # The local-only reject must not fire for broker endpoints; ISequenceAllocator is registered
        # so the (separate) allocator-presence guard is satisfied too.
        outbox = RecordingOutboxStore()
        unit_of_work = RecordingUoW()
        config = MessagingConfig(
            endpoints=[external_endpoint('ext://orders', partition_by=order_id_partition)],
            outbox=OutboxConfig(),
            transports={'ext': RecordingTransport},
            global_pipeline_behaviors=[TransactionalBehavior],
        )
        async with create_test_app(
            imports=[MessagingModule.register(config)],
            providers=[
                object_(unit_of_work, provided_type=IUnitOfWork),
                object_(RecordingAllocator(), provided_type=ISequenceAllocator),
                object_(outbox, provided_type=IOutboxStore),
                object_(_durability(unit_of_work=unit_of_work, outbox=outbox), provided_type=IDurabilityStore),
            ],
        ):
            pass

    @staticmethod
    def test_local_broker_uri_collision_raises() -> None:
        config = MessagingConfig(
            endpoints=[local_queue('orders'), listen('orders')],
            inbox=InboxConfig(),
            transports={'orders': RecordingTransport},
        )
        with pytest.raises(ImproperlyConfiguredError, match='must not share a URI'):
            MessagingModule.register(config)

    @staticmethod
    def test_invoke_scheme_endpoint_raises() -> None:
        config = MessagingConfig(
            endpoints=[local_queue('invoke://x')],
        )
        with pytest.raises(ImproperlyConfiguredError, match="scheme 'invoke' is reserved"):
            MessagingModule.register(config)

    @staticmethod
    async def test_local_broker_distinct_uri_namespaces_does_not_raise() -> None:
        inbox = FakeInboxStore()
        unit_of_work = RecordingUoW()
        config = MessagingConfig(
            endpoints=[local_queue('local://orders'), listen('rabbitmq://orders')],
            inbox=InboxConfig(),
            transports={'rabbitmq': RecordingTransport},
            global_pipeline_behaviors=[TransactionalBehavior],
        )
        async with create_test_app(
            imports=[MessagingModule.register(config)],
            providers=[
                object_(unit_of_work, provided_type=IUnitOfWork),
                object_(inbox, provided_type=IInboxStore),
                object_(RecordingAllocator(), provided_type=ISequenceAllocator),
                object_(_durability(unit_of_work=unit_of_work, inbox=inbox), provided_type=IDurabilityStore),
            ],
        ):
            pass

    @staticmethod
    async def test_dead_letter_worker_starts_when_auto_replay_enabled() -> None:
        dead_letters = RecordingDeadLetterStore()
        unit_of_work = RecordingUoW()
        config = MessagingConfig(
            dead_letter=DeadLetterConfig(auto_replay_enabled=True),
        )
        async with create_test_app(
            imports=[MessagingModule.register(config)],
            providers=[
                object_(unit_of_work, provided_type=IUnitOfWork),
                object_(dead_letters, provided_type=IDeadLetterStore),
                object_(
                    _durability(unit_of_work=unit_of_work, dead_letters=dead_letters),
                    provided_type=IDurabilityStore,
                ),
            ],
        ):
            pass  # the lifecycle hooks start + stop the worker without error

    @staticmethod
    def test_no_maintenance_owner_when_dead_letter_store_only() -> None:
        dynamic = MessagingModule.register(
            MessagingConfig(dead_letter=DeadLetterConfig()),
        )
        assert not any(isinstance(ext, DurabilityMaintenanceLifecycleExtension) for ext in dynamic.extensions)

    @staticmethod
    def test_maintenance_owner_when_dead_letter_retention_set() -> None:
        dynamic = MessagingModule.register(
            MessagingConfig(
                dead_letter=DeadLetterConfig(retention=timedelta(days=30)),
            ),
        )
        assert any(isinstance(ext, DurabilityMaintenanceLifecycleExtension) for ext in dynamic.extensions)
