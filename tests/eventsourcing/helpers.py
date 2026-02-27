from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine


def fail_save_n_times(
    original_save: Callable[..., Coroutine[Any, Any, Any]],
    error: Exception,
    fail_count: int = 1,
) -> Callable[..., Coroutine[Any, Any, Any]]:
    calls = 0

    async def side_effect(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        if calls <= fail_count:
            raise error
        return await original_save(*args, **kwargs)

    return side_effect
