from __future__ import annotations

import logging
import signal

import anyio

logger = logging.getLogger(__name__)


async def wait_for_shutdown(event: anyio.Event) -> None:
    try:
        with anyio.open_signal_receiver(signal.SIGTERM, signal.SIGINT) as signals:
            async for signum in signals:  # pragma: no cover - signal delivery not exercised under test
                logger.info('Shutdown signal received: %s', signum.name)
                event.set()
                return
    except NotImplementedError:
        await event.wait()
