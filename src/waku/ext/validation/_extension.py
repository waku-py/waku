from __future__ import annotations

import warnings
from dataclasses import dataclass
from itertools import chain
from typing import TYPE_CHECKING, Final

from waku.extensions import AfterApplicationInit

if TYPE_CHECKING:
    from collections.abc import Sequence

    from waku.application import Application
    from waku.ext.validation import ValidationRule
    from waku.ext.validation._errors import ValidationError


@dataclass(frozen=True, slots=True, kw_only=True)
class ValidationContext:
    app: Application


class ValidationExtension(AfterApplicationInit):
    def __init__(self, rules: Sequence[ValidationRule], *, strict: bool = True) -> None:
        self.rules = rules
        self.strict: Final = strict

    def after_app_init(self, app: Application) -> None:
        context = ValidationContext(app=app)

        errors_chain = chain.from_iterable(rule.validate(context) for rule in self.rules)
        if errors := list(errors_chain):
            self._raise(errors)

    def _raise(self, errors: Sequence[ValidationError]) -> None:
        if self.strict:
            msg = 'Validation error'
            raise ExceptionGroup(msg, errors)

        for error in errors:
            warnings.warn(str(error), stacklevel=3)
