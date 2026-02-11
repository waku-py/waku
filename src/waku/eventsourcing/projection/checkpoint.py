from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

__all__ = ['Checkpoint']


@dataclass(frozen=True, slots=True, kw_only=True)
class Checkpoint:
    projection_name: str
    position: int
    updated_at: datetime
