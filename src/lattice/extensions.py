from __future__ import annotations

import typing
from typing import Protocol

if typing.TYPE_CHECKING:
    from lattice.modules import Application


@typing.runtime_checkable
class OnApplicationInit(Protocol):
    def on_init(self, app: Application) -> None: ...


ApplicationExtension = OnApplicationInit
