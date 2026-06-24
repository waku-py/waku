import anyio

from waku import module
from waku.extensions import DEFAULT_EXTENSIONS
from waku.factory import WakuFactory

from tests._lifecycle import LifecycleRecorder


@module()
class _EmptyModule:
    pass


async def test_run_enters_then_request_shutdown_exits_gracefully() -> None:
    recorder = LifecycleRecorder()
    app = WakuFactory(_EmptyModule, extensions=[*DEFAULT_EXTENSIONS, recorder]).create()

    with anyio.fail_after(5):
        async with anyio.create_task_group() as tg:
            tg.start_soon(app.run)
            await recorder.initialized.wait()
            app.request_shutdown()

    assert recorder.events == ['init', 'shutdown']


async def test_run_returns_immediately_when_shutdown_already_requested() -> None:
    app = WakuFactory(_EmptyModule).create()
    app.request_shutdown()
    with anyio.fail_after(5):
        await app.run()
