from __future__ import annotations

from typing import TYPE_CHECKING, Any, ParamSpec, Protocol, Self, TypeVar, overload

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine, Iterable
    from types import TracebackType

    from waku.di._utils import Dependency

_T = TypeVar('_T')
_P = ParamSpec('_P')


class InjectionContext(Protocol):
    async def resolve(self, type_: type[_T]) -> _T: ...

    async def resolve_iterable(self, type_: type[_T]) -> list[_T]: ...

    @overload
    async def execute(
        self,
        function: Callable[_P, Coroutine[Any, Any, _T]],
        dependencies: Iterable[Dependency[object]],
        *args: _P.args,
        **kwargs: _P.kwargs,
    ) -> _T: ...

    @overload
    async def execute(
        self,
        function: Callable[_P, _T],
        dependencies: Iterable[Dependency[object]],
        *args: _P.args,
        **kwargs: _P.kwargs,
    ) -> _T: ...

    async def execute(
        self,
        function: Callable[_P, Coroutine[Any, Any, _T] | _T],
        dependencies: Iterable[Dependency[object]],
        *args: _P.args,
        **kwargs: _P.kwargs,
    ) -> _T: ...

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None: ...
