from __future__ import annotations

import warnings
from dataclasses import dataclass
from itertools import chain
from typing import TYPE_CHECKING, Final

from waku.extensions import AfterApplicationInit

if TYPE_CHECKING:
    from collections.abc import Sequence

    from waku.application import WakuApplication
    from waku.validation import ValidationRule
    from waku.validation._errors import ValidationError

__all__ = [
    'ValidationContext',
    'ValidationExtension',
]


@dataclass(frozen=True, slots=True, kw_only=True)
class ValidationContext:
    app: WakuApplication


class ValidationExtension(AfterApplicationInit):
    __slots__ = ('rules', 'strict')

    def __init__(self, rules: Sequence[ValidationRule], *, strict: bool = True) -> None:
        self.rules = rules
        self.strict: Final = strict

    async def after_app_init(self, app: WakuApplication) -> None:
        context = ValidationContext(app=app)

        errors_chain = chain.from_iterable(rule.validate(context) for rule in self.rules)
        if errors := list(errors_chain):
            self._raise(errors)

    def _raise(self, errors: list[ValidationError]) -> None:
        if self.strict:
            msg = 'Validation error'
            raise ExceptionGroup(msg, errors)

        for error in errors:
            warnings.warn(str(error), stacklevel=3)
