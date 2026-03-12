from waku.messaging import IMessageBus
from waku.testing import create_test_app

from app.commands import OpenAccountCommand
from app.modules import AppModule


async def test_full_flow() -> None:
    async with create_test_app(base=AppModule) as app:
        async with app.container() as container:
            bus = await container.get(IMessageBus)
            result = await bus.invoke(OpenAccountCommand(account_id='acc-1', owner='dex'))
            assert result.account_id == 'acc-1'
