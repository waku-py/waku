from __future__ import annotations

from typing import TYPE_CHECKING, Any, Self

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine


class RecordingContext:
    def __init__(self) -> None:
        self.entered = 0
        self.exited = 0
        self.exit_exceptions: list[type[BaseException] | None] = []

    async def __aenter__(self) -> Self:
        self.entered += 1
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        self.exited += 1
        self.exit_exceptions.append(exc_type)


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
