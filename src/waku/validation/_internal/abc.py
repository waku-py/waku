from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from waku.validation._internal.errors import ValidationError
    from waku.validation._internal.extension import ValidationContext

__all__ = ['ValidationRule']


@runtime_checkable
class ValidationRule(Protocol):
    def validate(self, context: ValidationContext) -> list[ValidationError]: ...
