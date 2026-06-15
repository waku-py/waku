from __future__ import annotations

from waku.messaging import DeadLetterConfig
from waku.messaging.errors import DeadLetterQuery, ReplayExecutor


def test_dead_letter_management_public_exports() -> None:
    assert DeadLetterConfig is not None
    assert DeadLetterQuery is not None
    assert ReplayExecutor is not None
