from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any

import pytest
from typing_extensions import override

from waku.di import singleton
from waku.messaging import (
    IMessageBus,
    IRequest,
    MessagingConfig,
    MessagingExtension,
    MessagingModule,
)
from waku.messaging.endpoints import ExecutionOutcome
from waku.messaging.handler import MessageHandler, RequestHandler
from waku.messaging.router import local_queue, route
from waku.messaging.testing import MessageTracker, TrackingEvent, TrackingMessageObserver
from waku.testing import create_test_app

from tests._wait import wait_until
from tests.messaging.helpers import make_envelope

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


@dataclass(frozen=True, slots=True)
class _Order(IRequest[None]):
    order_id: str = ''


@dataclass(frozen=True, slots=True)
class _Boom(IRequest[None]):
    pass


@dataclass(frozen=True, slots=True)
class _NeverSent(IRequest[None]):
    pass


class _BoomError(Exception):
    pass


class _OrderHandler(RequestHandler[_Order, None]):
    @override
    async def handle(self, request: _Order, /) -> None: ...


class _BoomHandler(RequestHandler[_Boom, None]):
    @override
    async def handle(self, request: _Boom, /) -> None:
        raise _BoomError


def _order_config() -> MessagingConfig:
    return MessagingConfig(
        endpoints=[local_queue('the-q')],
        routing=[route(_Order).to('the-q')],
        observers=(TrackingMessageObserver,),
    )


@asynccontextmanager
async def _tracked_app(
    config: MessagingConfig,
    *handlers: type[MessageHandler[Any, Any]],
) -> AsyncIterator[tuple[MessageTracker, IMessageBus]]:
    async with (
        create_test_app(
            imports=[MessagingModule.register(config)],
            extensions=[MessagingExtension().bind(*handlers)],
            providers=[singleton(MessageTracker)],
        ) as app,
        app.container() as container,
    ):
        tracker = await container.get(MessageTracker)
        bus = await container.get(IMessageBus)
        yield tracker, bus


class TestRecordingAndReadViews:
    @staticmethod
    async def test_executed_record_captures_payload_outcome_event_and_destination() -> None:
        order = _Order(order_id='o-1')
        async with _tracked_app(_order_config(), _OrderHandler) as (tracker, bus):
            await bus.send(order)
            await wait_until(lambda: len(tracker.executed_of(_Order)) >= 1)

            [record] = tracker.executed_of(_Order)
            assert record.payload == order
            assert record.outcome is ExecutionOutcome.SUCCESS
            assert record.event is TrackingEvent.EXECUTED
            assert record.destination == 'the-q'

    @staticmethod
    async def test_sent_record_has_no_outcome() -> None:
        async with _tracked_app(_order_config(), _OrderHandler) as (tracker, bus):
            await bus.send(_Order(order_id='o-1'))
            await wait_until(lambda: any(r.event is TrackingEvent.SENT for r in tracker.sent))

            [sent] = [r for r in tracker.sent if isinstance(r.payload, _Order)]
            assert sent.event is TrackingEvent.SENT
            assert sent.outcome is None
            assert sent.destination == 'the-q'

    @staticmethod
    async def test_single_dedups_sent_and_executed_records_for_one_message() -> None:
        order = _Order(order_id='o-1')
        async with _tracked_app(_order_config(), _OrderHandler) as (tracker, bus):
            await bus.send(order)
            await wait_until(lambda: bool(tracker.executed_of(_Order)) and bool(tracker.sent))

            assert tracker.single(_Order) == order

    @staticmethod
    async def test_single_raises_when_nothing_recorded() -> None:
        async with _tracked_app(_order_config(), _OrderHandler) as (tracker, _bus):
            with pytest.raises(ValueError, match='_Order'):
                tracker.single(_Order)

    @staticmethod
    async def test_single_raises_when_two_distinct_messages_recorded() -> None:
        async with _tracked_app(_order_config(), _OrderHandler) as (tracker, bus):
            await bus.send(_Order(order_id='o-1'))
            await bus.send(_Order(order_id='o-2'))
            await wait_until(lambda: len(tracker.executed_of(_Order)) >= 2)

            with pytest.raises(ValueError, match='_Order'):
                tracker.single(_Order)

    @staticmethod
    async def test_container_resolved_tracker_reflects_observer_writes() -> None:
        order = _Order(order_id='o-1')
        async with _tracked_app(_order_config(), _OrderHandler) as (tracker, bus):
            await bus.send(order)
            await wait_until(lambda: bool(tracker.executed_of(_Order)))

            assert any(r.payload == order for r in tracker.executed)

    @staticmethod
    async def test_failing_handler_records_exception_and_terminal_outcome() -> None:
        config = MessagingConfig(
            endpoints=[local_queue('boom-q')],
            routing=[route(_Boom).to('boom-q')],
            observers=(TrackingMessageObserver,),
        )
        async with _tracked_app(config, _BoomHandler) as (tracker, bus):
            await bus.send(_Boom())
            await wait_until(lambda: len(tracker.executed_of(_Boom)) >= 1)

            [record] = tracker.executed_of(_Boom)
            assert isinstance(record.exc, _BoomError)
            assert record.outcome is ExecutionOutcome.FAILED_NO_POLICY
            assert any(isinstance(exc, _BoomError) for exc in tracker.exceptions)


class TestSleepFreeWaits:
    @staticmethod
    async def test_wait_for_executed_returns_when_message_executes_after_wait_starts() -> None:
        order = _Order(order_id='o-1')
        async with _tracked_app(_order_config(), _OrderHandler) as (tracker, bus):
            await bus.send(order)

            records = await tracker.wait_for_executed(_Order)

            assert [r.payload for r in records] == [order]

    @staticmethod
    async def test_wait_for_executed_returns_immediately_when_already_executed() -> None:
        async with _tracked_app(_order_config(), _OrderHandler) as (tracker, bus):
            await bus.send(_Order(order_id='o-1'))
            await tracker.wait_for_executed(_Order)

            records = await tracker.wait_for_executed(_Order, deadline=0.001)

            assert len(records) == 1

    @staticmethod
    async def test_wait_for_executed_count_requires_distinct_message_ids() -> None:
        tracker = MessageTracker()
        observer = TrackingMessageObserver(tracker)
        envelope = make_envelope(_Order(order_id='o-1'))

        await observer.on_executed(envelope, 'q', _OrderHandler, ExecutionOutcome.SUCCESS, None, timedelta())
        await observer.on_executed(envelope, 'q', _OrderHandler, ExecutionOutcome.SUCCESS, None, timedelta())

        with pytest.raises(TimeoutError):
            await tracker.wait_for_executed(_Order, count=2, deadline=0.05)

        records = await tracker.wait_for_executed(_Order, count=1, deadline=0.05)
        assert len({r.message_id for r in records}) == 1

    @staticmethod
    async def test_wait_for_executed_outcome_filter_ignores_other_outcomes() -> None:
        tracker = MessageTracker()
        observer = TrackingMessageObserver(tracker)
        succeeded = make_envelope(_Order(order_id='ok'))
        failed = make_envelope(_Order(order_id='bad'))

        await observer.on_executed(succeeded, 'q', _OrderHandler, ExecutionOutcome.SUCCESS, None, timedelta())
        await observer.on_executed(
            failed, 'q', _OrderHandler, ExecutionOutcome.FAILED_NO_POLICY, _BoomError(), timedelta()
        )

        records = await tracker.wait_for_executed(_Order, outcome=ExecutionOutcome.SUCCESS, deadline=0.05)
        assert len(records) == 1
        assert records[0].outcome is ExecutionOutcome.SUCCESS

        with pytest.raises(TimeoutError):
            await tracker.wait_for_executed(_Order, outcome=ExecutionOutcome.SUCCESS, count=2, deadline=0.05)

    @staticmethod
    async def test_wait_for_executed_times_out_with_activity_dump() -> None:
        async with _tracked_app(_order_config(), _OrderHandler) as (tracker, bus):
            await bus.send(_Order(order_id='o-1'))
            await tracker.wait_for_executed(_Order)

            with pytest.raises(TimeoutError, match='_Order'):
                await tracker.wait_for_executed(_NeverSent, deadline=0.05)

    @staticmethod
    async def test_wait_for_sent_completes_on_sent_hook() -> None:
        order = _Order(order_id='o-1')
        async with _tracked_app(_order_config(), _OrderHandler) as (tracker, bus):
            await bus.send(order)

            records = await tracker.wait_for_sent(_Order)

            assert records[0].event is TrackingEvent.SENT
