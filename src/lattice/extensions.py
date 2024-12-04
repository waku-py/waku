from typing import Protocol
import typing

from lattice.module import Application


@typing.runtime_checkable
class OnApplicationInit(Protocol):
    def on_init(self, app: Application) -> None:
        ...


ApplicationExtension = OnApplicationInit
