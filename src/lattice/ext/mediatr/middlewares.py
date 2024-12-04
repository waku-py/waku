class Middleware(Protocol[R, RRes]):
    async def __call__(
        self,
        handler: Handler[R, RRes],
        request: R,
    ) -> RRes:
        return await handler(request)


def wrap_middleware(
    middlewares: Sequence[Middleware[R, RRes]],
    handler: Handler[R, RRes],
) -> Handler[R, RRes]:
    for middleware in reversed(middlewares):
        handler = functools.partial(middleware, handler)

    return handler
