from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Generic, TypeAlias, TypeVar

__all__ = ['Inject', 'Injected']

_T = TypeVar('_T')


@dataclass(slots=True)
class Inject:
    pass


if TYPE_CHECKING:
    Injected: TypeAlias = Annotated[_T, Inject]

else:

    class Injected(Generic[_T]):
        def __class_getitem__(cls, item: object) -> object:
            return Annotated[item, Inject]
