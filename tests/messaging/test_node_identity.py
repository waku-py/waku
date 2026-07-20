from __future__ import annotations

from waku import NodeIdentity
from waku.messaging import MessagingConfig, MessagingModule
from waku.testing import create_test_app


async def test_configured_node_description_reaches_the_process_identity() -> None:
    config = MessagingConfig(node_description='orders-worker-3')

    async with create_test_app(imports=[MessagingModule.register(config)]) as app:
        identity = await app.container.get(NodeIdentity)

    assert identity.description == 'orders-worker-3'


async def test_one_identity_is_shared_across_scopes() -> None:
    async with create_test_app(imports=[MessagingModule.register(MessagingConfig())]) as app:
        app_identity = await app.container.get(NodeIdentity)
        async with app.container() as request_container:
            request_identity = await request_container.get(NodeIdentity)

    assert request_identity is app_identity
