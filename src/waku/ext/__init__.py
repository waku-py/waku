from __future__ import annotations

from typing import TYPE_CHECKING

from waku.ext.validation import ValidationExtension
from waku.ext.validation.rules import DependenciesAccessibleRule

if TYPE_CHECKING:
    from collections.abc import Sequence

    from waku.extensions import ApplicationExtension

__all__ = ['DEFAULT_EXTENSIONS']

DEFAULT_EXTENSIONS: Sequence[ApplicationExtension] = (
    ValidationExtension(
        [DependenciesAccessibleRule()],
        strict=True,
    ),
)
