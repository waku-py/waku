import asyncio

from waku import WakuFactory
from waku.cqrs import IMediator

from app.commands import DepositCommand, OpenAccountCommand
from app.modules import AppModule


async def main() -> None:
    app = WakuFactory(AppModule).create()

    async with app, app.container() as container:
        mediator = await container.get(IMediator)

        await mediator.send(OpenAccountCommand(account_id='acc-1', owner='dex'))
        result = await mediator.send(DepositCommand(account_id='acc-1', amount=500))
        print(f'Balance: {result.balance}')


if __name__ == '__main__':
    asyncio.run(main())
