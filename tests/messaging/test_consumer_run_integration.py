# ruff: noqa: E402
from __future__ import annotations

import anyio
import pytest

faststream_rabbit = pytest.importorskip('faststream.rabbit')

from faststream.rabbit import TestRabbitBroker

from waku import module
from waku.di import object_
from waku.extensions import DEFAULT_EXTENSIONS
from waku.factory import WakuFactory
from waku.messaging import InboxConfig, MessagingConfig, MessagingModule, TransactionalBehavior
from waku.messaging.endpoints.base import listen
from waku.messaging.transport.faststream.rabbitmq import FastStreamRabbitTransport
from waku.uow import IUnitOfWork

from tests._lifecycle import LifecycleRecorder
from tests.messaging.helpers import FakeUoW
from tests.messaging.inbox.fake_store import FakeInboxStore


async def test_consumer_only_app_run_drives_graceful_shutdown() -> None:
    inbox = FakeInboxStore()
    transport = FastStreamRabbitTransport(url='amqp://x')
    config = MessagingConfig(
        endpoints=[listen('rabbitmq://orders')],
        inbox=InboxConfig(store=lambda: inbox, owner_id='test-node:1'),
        transports={'rabbitmq': lambda: transport},
        global_pipeline_behaviors=[TransactionalBehavior],
    )

    @module(
        imports=[MessagingModule.register(config)],
        providers=[object_(FakeUoW(), provided_type=IUnitOfWork)],
    )
    class _ConsumerModule:
        pass

    recorder = LifecycleRecorder()
    app = WakuFactory(_ConsumerModule, extensions=[*DEFAULT_EXTENSIONS, recorder]).create()

    async with TestRabbitBroker(transport._send_broker, transport._listen_broker):  # noqa: SLF001
        with anyio.fail_after(5):
            async with anyio.create_task_group() as tg:
                tg.start_soon(app.run)
                await recorder.initialized.wait()
                app.request_shutdown()

    assert recorder.events == ['init', 'shutdown']
