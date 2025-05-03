from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from waku.validation._errors import ValidationError
    from waku.validation._extension import ValidationContext

__all__ = ['ValidationRule']


class ValidationRule(Protocol):
    def validate(self, context: ValidationContext) -> list[ValidationError]: ...
