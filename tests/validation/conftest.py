import pytest

from waku.ext.validation import ValidationRule
from waku.ext.validation.rules import DependenciesAccessible, DIScopeMismatch


@pytest.fixture
def rules() -> list[ValidationRule]:
    return [DependenciesAccessible(), DIScopeMismatch()]
