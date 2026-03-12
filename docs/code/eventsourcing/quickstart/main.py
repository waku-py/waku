import asyncio

from waku import WakuFactory
from waku.messaging import IMessageBus

from app.commands import DepositCommand, OpenAccountCommand
from app.modules import AppModule


async def main() -> None:
    app = WakuFactory(AppModule).create()

    async with app, app.container() as container:
        bus = await container.get(IMessageBus)

        await bus.invoke(OpenAccountCommand(account_id='acc-1', owner='dex'))
        result = await bus.invoke(DepositCommand(account_id='acc-1', amount=500))
        print(f'Balance: {result.balance}')


if __name__ == '__main__':
    asyncio.run(main())
