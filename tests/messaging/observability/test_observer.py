import asyncio
import logging
from datetime import timedelta
from typing import Any, cast

import pytest
from typing_extensions import override

from waku.messaging.contracts.envelope import MessageEnvelope
from waku.messaging.contracts.handler import HandlerType
from waku.messaging.endpoints.executor import ExecutionOutcome
from waku.messaging.observability.observer import IMessageObserver, MessageObservers, ObserverPlan


class _Recorder(IMessageObserver):
    def __init__(self) -> None:
        self.calls: list[str] = []

    @override
    async def on_sent(self, envelope: MessageEnvelope[Any], destination: str) -> None:
        self.calls.append(f'sent:{destination}')


class _Raiser(IMessageObserver):
    @override
    async def on_sent(self, envelope: MessageEnvelope[Any], destination: str) -> None:
        msg = 'boom'
        raise RuntimeError(msg)


pytestmark = pytest.mark.anyio


async def test_fanout_invokes_all_observers() -> None:
    a, b = _Recorder(), _Recorder()
    await MessageObservers([a, b]).sent(cast('MessageEnvelope[Any]', object()), 'queue-a')
    assert a.calls == b.calls == ['sent:queue-a']


async def test_raising_observer_is_swallowed_and_logged(caplog: pytest.LogCaptureFixture) -> None:
    rec = _Recorder()
    with caplog.at_level(logging.WARNING):
        await MessageObservers([_Raiser(), rec]).sent(
            cast('MessageEnvelope[Any]', object()), 'queue-a'
        )  # must not raise
    assert rec.calls == ['sent:queue-a']  # later observer still ran
    assert any('observer' in r.message.lower() for r in caplog.records)


async def test_empty_list_is_noop() -> None:
    await MessageObservers([]).executed(
        cast('MessageEnvelope[Any]', object()),
        'q',
        cast('HandlerType', object),
        ExecutionOutcome.SUCCESS,
        None,
        timedelta(),
    )


async def test_default_methods_are_noop() -> None:
    class _Bare(IMessageObserver):
        pass

    observers = MessageObservers([_Bare()])
    await observers.sent(cast('MessageEnvelope[Any]', object()), 'q')
    await observers.executing(cast('MessageEnvelope[Any]', object()), 'q', cast('HandlerType', object))
    await observers.executed(
        cast('MessageEnvelope[Any]', object()),
        'q',
        cast('HandlerType', object),
        ExecutionOutcome.SUCCESS,
        None,
        timedelta(),
    )


async def test_cancellederror_is_not_swallowed() -> None:
    class _Canceller(IMessageObserver):
        @override
        async def on_sent(self, envelope: MessageEnvelope[Any], destination: str) -> None:
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await MessageObservers([_Canceller()]).sent(cast('MessageEnvelope[Any]', object()), 'q')


async def test_sync_observer_raising_at_call_time_is_swallowed(caplog: pytest.LogCaptureFixture) -> None:
    class _SyncRaiser(IMessageObserver):
        def on_sent(  # type: ignore[override]  # noqa: PLR6301
            self,
            envelope: MessageEnvelope[Any],  # noqa: ARG002
            destination: str,  # noqa: ARG002
        ) -> None:
            msg = 'call-time boom'
            raise RuntimeError(msg)

    rec = _Recorder()
    with caplog.at_level(logging.WARNING):
        await MessageObservers([_SyncRaiser(), rec]).sent(cast('MessageEnvelope[Any]', object()), 'q')
    assert rec.calls == ['sent:q']  # later observer still ran
    assert any('observer' in r.message.lower() for r in caplog.records)


def test_for_endpoint_returns_the_per_uri_set_for_a_known_uri() -> None:
    global_observers = MessageObservers([_Recorder()])
    per_uri_observers = MessageObservers([_Recorder()])
    plan = ObserverPlan(global_observers, {'queue-a': per_uri_observers})

    assert plan.for_endpoint('queue-a') is per_uri_observers


def test_for_endpoint_returns_the_global_set_for_an_unknown_uri() -> None:
    global_observers = MessageObservers([_Recorder()])
    plan = ObserverPlan(global_observers, {'queue-a': MessageObservers([_Recorder()])})

    assert plan.for_endpoint('queue-b') is global_observers


def test_global_observers_property_returns_the_constructed_instance() -> None:
    global_observers = MessageObservers([_Recorder()])
    plan = ObserverPlan(global_observers, {})

    assert plan.global_observers is global_observers
