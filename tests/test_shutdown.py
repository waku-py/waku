import anyio
import anyio.lowlevel
import pytest

from waku._internal.shutdown import wait_for_shutdown


async def test_wait_for_shutdown_falls_back_to_event_when_signals_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise(*_args: object, **_kwargs: object) -> None:
        raise NotImplementedError

    monkeypatch.setattr(anyio, 'open_signal_receiver', _raise)
    event = anyio.Event()

    with anyio.fail_after(2):
        async with anyio.create_task_group() as tg:
            tg.start_soon(wait_for_shutdown, event)
            await anyio.lowlevel.checkpoint()
            event.set()
