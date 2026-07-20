from __future__ import annotations

from uuid import UUID

from adaptix import Retort, dumper, loader

__all__ = ['default_retort']

default_retort = Retort(
    recipe=[
        loader(UUID, UUID),
        dumper(UUID, str),
    ],
)
