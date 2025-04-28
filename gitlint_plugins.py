"""This module provides custom gitlint rules to enforce Conventional Commits format.

See https://www.conventionalcommits.org/en/v1.0.0/.
"""

import re
import tomllib
from pathlib import Path
from typing import ClassVar, Final

from gitlint.git import GitCommit
from gitlint.rules import CommitMessageTitle, LineRule, RuleViolation

BASE_DIR: Final = Path(__file__).parent

COMMIT_PATTERN: Final = r'^(?P<type>[^(]+?)(\((?P<context>[^)]+?)\))?!?:\s.+$'
RULE_REGEX: Final = re.compile(COMMIT_PATTERN)

CONVENTIONAL_COMMIT_ERROR: Final = (
    "Commit title does not follow ConventionalCommits.org format 'type(optional-scope): description'"
)


class ConventionalCommitTitle(LineRule):  # type: ignore[misc]
    name = 'conventional-commit-title'
    id = 'CT1'
    target = CommitMessageTitle

    contexts: ClassVar[tuple[str, ...]] = (
        'core',
        'deps',
        'di',
        'ext',
        'infra',
        'linters',
        'mediator',
        'release',
        'tests',
        'validation',
    )
    default_types: ClassVar[tuple[str, ...]] = (
        'build',
        'chore',
        'ci',
        'docs',
        'feat',
        'fix',
        'perf',
        'refactor',
        # 'revert',  # currently unsupported in python-semantic-release
        'style',
        'test',
    )

    def validate(self, line: str, _: GitCommit) -> list[RuleViolation]:
        """Validate a commit message line.

        Args:
            line: The commit message line to validate.
            _: The git commit object (unused).

        Returns:
            List of rule violations found in the commit message.
        """
        if self._is_special_commit(line):
            return []

        match = RULE_REGEX.match(line)
        if not match:
            return [RuleViolation(self.id, CONVENTIONAL_COMMIT_ERROR, line)]

        return self._validate_match(match, line)

    def _is_special_commit(self, line: str) -> bool:  # noqa: PLR6301
        """Check if the commit is a special type that should be ignored.

        Args:
            line: The commit message line.

        Returns:
            True if the commit should be ignored, False otherwise.
        """
        return line.startswith(('Draft:', 'WIP:', 'Merge'))

    def _validate_match(self, match: re.Match[str], line: str) -> list[RuleViolation]:
        """Validate the components of a matched commit message.

        Args:
            match: The regex match object containing commit components.
            line: The original commit message line.

        Returns:
            List of rule violations found in the commit components.
        """
        violations: list[RuleViolation] = []

        type_ = match.group('type')
        context = match.group('context')

        violations.extend(self._validate_type(type_, line))
        if context:
            violations.extend(self._validate_context(context, line))

        return violations

    def _validate_type(self, type_: str, line: str) -> list[RuleViolation]:
        """Validate the commit type.

        Args:
            type_: The commit type to validate.
            line: The original commit message line.

        Returns:
            List of violations if the type is invalid.
        """
        allowed_types = self._get_types()
        if type_ not in allowed_types:
            opt_str = ', '.join(sorted(allowed_types))
            return [RuleViolation(self.id, f'Commit type {type_} is not one of {opt_str}', line)]
        return []

    def _validate_context(self, context: str, line: str) -> list[RuleViolation]:
        """Validate the commit context.

        Args:
            context: The commit context to validate.
            line: The original commit message line.

        Returns:
            List of violations if the context is invalid.
        """
        allowed_contexts = set(self.contexts)
        if context not in allowed_contexts:
            opt_str = ', '.join(sorted(allowed_contexts))
            return [RuleViolation(self.id, f'Commit context is not one of {opt_str}', line)]
        return []

    def _get_types(self) -> set[str]:
        """Get allowed commit types from pyproject.toml or defaults.

        Returns:
            Set of allowed commit types.
        """
        try:
            pyproject = tomllib.loads(BASE_DIR.joinpath('pyproject.toml').read_text())
            allowed_types = pyproject['tool']['semantic_release']['commit_parser_options']['allowed_tags']
        except (KeyError, tomllib.TOMLDecodeError):
            self.log.exception('Failed to load commit types from pyproject.toml')
            allowed_types = self.default_types

        return set(allowed_types)
