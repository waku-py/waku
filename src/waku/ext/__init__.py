from __future__ import annotations

from typing import TYPE_CHECKING

from waku.ext.validation import ValidationExtension
from waku.ext.validation.rules import DependenciesAccessible, DIScopeMismatch

if TYPE_CHECKING:
    from collections.abc import Sequence

    from waku.extensions import Extension

__all__ = ['DEFAULT_EXTENSIONS']

DEFAULT_EXTENSIONS: Sequence[Extension] = (
    ValidationExtension(
        [
            DependenciesAccessible(),
            DIScopeMismatch(),
        ],
        strict=True,
    ),
)
