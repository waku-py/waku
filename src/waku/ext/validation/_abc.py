from typing import Protocol

from waku.ext.validation._errors import ValidationError
from waku.ext.validation._extension import ValidationContext

__all__ = ['ValidationRule']


class ValidationRule(Protocol):
    def validate(self, context: ValidationContext) -> list[ValidationError]: ...
