from collections.abc import Callable
from datetime import UTC, datetime
from typing import TypeAlias

__all__ = [
    'Now',
    'utc_now',
]

# Runtime alias (not a string): dishka introspects __init__ via get_type_hints; TYPE_CHECKING-only would fail.
Now: TypeAlias = Callable[[], datetime]


def utc_now() -> datetime:
    return datetime.now(tz=UTC)
