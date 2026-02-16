import contextlib
from collections.abc import AsyncIterator

import uvicorn
from dishka.integrations.fastapi import inject, setup_dishka
from fastapi import FastAPI

from waku import WakuFactory, module
from waku.di import Injected, scoped


class GreetingService:
    async def greet(self, name: str) -> str:
        return f'Hello, {name}!'


@module(providers=[scoped(GreetingService)])
class AppModule:
    pass


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    async with app.state.waku:  # (1)!
        yield


app = FastAPI(lifespan=lifespan)
waku_app = WakuFactory(AppModule).create()
app.state.waku = waku_app
setup_dishka(waku_app.container, app)  # (2)!


@app.get('/')
@inject  # (3)!
async def hello(greeting: Injected[GreetingService]) -> dict[str, str]:  # (4)!
    return {'message': await greeting.greet('waku')}


if __name__ == '__main__':
    uvicorn.run(app)
