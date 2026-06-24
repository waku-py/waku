from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

import pytest
from typing_extensions import override

from waku.di import is_registered, object_
from waku.messaging import (
    CallNext,
    EventHandler,
    IEvent,
    IMessageBus,
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
from waku.messaging.config import DeadLetterConfig
from waku.messaging.context import get_message_context
from waku.messaging.endpoints.base import external_endpoint, local_queue
from waku.messaging.errors.dead_letter import IDeadLetterStore
from waku.messaging.errors.policy import ErrorPolicy
from waku.messaging.errors.replay import ReplayExecutor
from waku.messaging.exceptions import (
    HandlerNotFound,
    ImproperlyConfiguredError,
    MultipleHandlersRegistered,
    NoRouteError,
)
from waku.messaging.modules import DeadLetterLifecycleExtension
from waku.messaging.pipeline.policy import BehaviorPlan
from waku.messaging.registry import MessageRegistry
from waku.testing import create_test_app
from waku.uow import IUnitOfWork

from tests.messaging.helpers import FakeUoW, RecordingDeadLetterStore, RecordingTransport, order_id_partition
from tests.messaging.outbox.fake_store import FakeOutboxStore

if TYPE_CHECKING:
    from uuid import UUID


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

    with pytest.raises(MultipleHandlersRegistered, match='_Command'):
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
        with pytest.raises(HandlerNotFound, match='No handler registered for _UnregisteredCommand'):
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
    command_context: dict[str, UUID] = {}
    event_context: dict[str, UUID] = {}

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
    assert event_context['causation_id'] == command_context['message_id']


class TestMessagingConfigValidation:
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
            default_error_policies=(ErrorPolicy.on_any_exception().move_to_dead_letter(),),
        )
        with pytest.raises(ImproperlyConfiguredError, match='dead_letter'):
            async with create_test_app(imports=[MessagingModule.register(config)]):
                pass  # pragma: no cover

    @staticmethod
    async def test_dead_letter_escalation_without_dead_letter_store_raises() -> None:
        config = MessagingConfig(
            default_error_policies=(ErrorPolicy.on_any_exception().retry(max_attempts=3).then_move_to_dead_letter(),),
        )
        with pytest.raises(ImproperlyConfiguredError, match='dead_letter'):
            async with create_test_app(imports=[MessagingModule.register(config)]):
                pass  # pragma: no cover

    @staticmethod
    async def test_dead_letter_store_without_uow_raises_at_startup() -> None:
        config = MessagingConfig(
            dead_letter=DeadLetterConfig(store=RecordingDeadLetterStore),
        )
        with pytest.raises(ImproperlyConfiguredError, match='IUnitOfWork is required'):
            async with create_test_app(imports=[MessagingModule.register(config)]):
                pass  # pragma: no cover

    @staticmethod
    async def test_durable_outbox_without_explicit_transactional_behavior_boots() -> None:
        config = MessagingConfig(
            outbox=OutboxConfig(store=FakeOutboxStore),
            transports={'test': RecordingTransport},
        )
        async with create_test_app(
            imports=[MessagingModule.register(config)],
            extensions=[MessagingExtension().bind(_CommandHandler)],
            providers=[object_(FakeUoW(), provided_type=IUnitOfWork)],
        ) as app:
            plan = await app.container.get(BehaviorPlan)
            registry = await app.container.get(MessageRegistry)
            handler_types = registry.handler_map.handler_types()
            assert handler_types
            assert all(TransactionalBehavior in plan.for_handler(handler_type) for handler_type in handler_types)

    @staticmethod
    def test_dead_letter_config_defaults() -> None:
        config = DeadLetterConfig(store=RecordingDeadLetterStore)
        assert config.auto_replay_enabled is False
        assert config.max_replay_count == 3
        assert config.retention is None

    @staticmethod
    async def test_dead_letter_config_registers_store_and_replay_executor() -> None:
        config = MessagingConfig(dead_letter=DeadLetterConfig(store=RecordingDeadLetterStore))
        async with (
            create_test_app(
                imports=[MessagingModule.register(config)],
                providers=[object_(FakeUoW(), provided_type=IUnitOfWork)],
            ) as app,
            app.container() as scope,
        ):
            assert await is_registered(scope, IDeadLetterStore)
            assert await is_registered(scope, ReplayExecutor)

    @staticmethod
    async def test_partition_by_without_allocator_raises_at_startup() -> None:
        config = MessagingConfig(
            endpoints=[external_endpoint('ext://orders', partition_by=order_id_partition)],
            outbox=OutboxConfig(store=FakeOutboxStore),
            transports={'ext': RecordingTransport},
            global_pipeline_behaviors=[TransactionalBehavior],
        )
        with pytest.raises(ImproperlyConfiguredError, match='ISequenceAllocator'):
            async with create_test_app(
                imports=[MessagingModule.register(config)],
                providers=[object_(FakeUoW(), provided_type=IUnitOfWork)],
            ):
                pass  # pragma: no cover

    @staticmethod
    async def test_buffered_local_queue_partition_by_does_not_require_allocator() -> None:
        # partition_by on a BUFFERED (in-memory) local queue is inert — no allocator is consulted, so
        # the guard must NOT fire.
        config = MessagingConfig(
            endpoints=[local_queue('q://orders', partition_by=order_id_partition)],
        )
        async with create_test_app(imports=[MessagingModule.register(config)]):
            pass

    @staticmethod
    async def test_dead_letter_worker_starts_when_auto_replay_enabled() -> None:
        config = MessagingConfig(
            dead_letter=DeadLetterConfig(store=RecordingDeadLetterStore, auto_replay_enabled=True),
        )
        async with create_test_app(
            imports=[MessagingModule.register(config)],
            providers=[object_(FakeUoW(), provided_type=IUnitOfWork)],
        ):
            pass  # the lifecycle hooks start + stop the worker without error

    @staticmethod
    def test_no_dead_letter_worker_when_store_only() -> None:
        dynamic = MessagingModule.register(
            MessagingConfig(dead_letter=DeadLetterConfig(store=RecordingDeadLetterStore)),
        )
        assert not any(isinstance(ext, DeadLetterLifecycleExtension) for ext in dynamic.extensions)

    @staticmethod
    def test_dead_letter_worker_when_retention_set() -> None:
        dynamic = MessagingModule.register(
            MessagingConfig(
                dead_letter=DeadLetterConfig(store=RecordingDeadLetterStore, retention=timedelta(days=30)),
            ),
        )
        assert any(isinstance(ext, DeadLetterLifecycleExtension) for ext in dynamic.extensions)
