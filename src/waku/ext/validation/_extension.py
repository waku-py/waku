from __future__ import annotations

import dataclasses
import warnings
from typing import TYPE_CHECKING, Final

from waku.extensions import OnApplicationInit

if TYPE_CHECKING:
    from collections.abc import Sequence

    from waku.application import Application
    from waku.ext.validation import ValidationRule
    from waku.ext.validation._errors import ValidationError


@dataclasses.dataclass(slots=True, kw_only=True, frozen=True)
class ValidationContext:
    app: Application


class ValidationExtension(OnApplicationInit):
    def __init__(self, rules: Sequence[ValidationRule], *, strict: bool = True) -> None:
        self.rules = rules
        self.strict: Final = strict

    def on_app_init(self, app: Application) -> None:
        context = ValidationContext(app=app)

        for rule in self.rules:
            if err := rule.validate(context):
                self._raise(err=err)

    def _raise(self, err: ValidationError) -> None:
        if self.strict:
            raise err
        warnings.warn(str(err), stacklevel=3)
