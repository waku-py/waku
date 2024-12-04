from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from lattice.modules import Application


@runtime_checkable
class OnApplicationInit(Protocol):
    def on_init(self, app: Application) -> None: ...


ApplicationExtension = OnApplicationInit
