import re
import tomllib
from pathlib import Path
from typing import ClassVar, Final

from gitlint.git import GitCommit
from gitlint.rules import CommitMessageTitle, LineRule, RuleViolation

BASE_DIR: Final = Path(__file__).parent

# https://www.conventionalcommits.org/en/v1.0.0/
# e.g.: feat(di): add new provider type
RULE_REGEX: Final = re.compile(r'^(?P<type>[^(]+?)(\((?P<context>[^)]+?)\))?!?:\s.+$')

CONVENTIONAL_COMMIT_DONT_MATCH_ERROR: Final = (
    "Commit title does not follow ConventionalCommits.org format 'type(optional-scope): description'"
)


class ConventionalCommitTitle(LineRule):  # type: ignore[misc]
    name = 'conventional-commit-title'
    id = 'CT1'
    target = CommitMessageTitle

    contexts: ClassVar[tuple[str, ...]] = ('core', 'di', 'ext', 'linters', 'tests', 'infra', 'deps')
    default_types: ClassVar[tuple[str, ...]] = (
        'fix',
        'feat',
        'chore',
        'docs',
        'style',
        'refactor',
        'perf',
        'test',
        # 'revert',  # на данный момент не поддерживается в python-semantic-release
        'ci',
        'build',
    )

    def validate(self, line: str, _: GitCommit) -> list[RuleViolation]:
        if line.startswith(('Draft:', 'WIP:')):
            return []

        match = RULE_REGEX.match(line)
        if not match:
            return [RuleViolation(self.id, CONVENTIONAL_COMMIT_DONT_MATCH_ERROR, line)]

        violations = []

        allowed_types = self._get_types()
        if (type_ := match.group('type')) not in allowed_types:
            opt_str = ', '.join(sorted(allowed_types))
            violations.append(RuleViolation(self.id, f'Commit type {type_} is not one of {opt_str}', line))

        allowed_contexts = set(self.contexts)
        if (context := match.group('context')) and context not in allowed_contexts:
            opt_str = ', '.join(sorted(allowed_contexts))
            violations.append(RuleViolation(self.id, f'Commit context is not one of {opt_str}', line))

        return violations

    def _get_types(self) -> set[str]:
        try:
            pyproject = tomllib.loads(BASE_DIR.joinpath('pyproject.toml').read_text())
            allowed_types = pyproject['tool']['semantic_release']['commit_parser_options']['allowed_tags']
        except (KeyError, tomllib.TOMLDecodeError):
            self.log.exception('Failed to load commit types from pyproject.toml')
            allowed_types = self.default_types

        return set(allowed_types)
