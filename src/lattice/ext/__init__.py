from __future__ import annotations

from typing import TYPE_CHECKING

from lattice.ext.validation import ValidationExtension

if TYPE_CHECKING:
    from collections.abc import Sequence

    from lattice.extensions import ApplicationExtension

__all__ = [
    'DEFAULT_EXTENSIONS',
]


DEFAULT_EXTENSIONS: Sequence[ApplicationExtension] = (ValidationExtension(strict=True),)
