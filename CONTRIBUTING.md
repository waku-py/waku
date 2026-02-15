# Contributing to Waku

## Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/getting-started/installation/) (package manager)
- [Task](https://taskfile.dev/installation/) (task runner; [shell completions](https://taskfile.dev/installation/#setup-completions) recommended)
- Git

## Development Setup

1. Fork the repository on GitHub, then clone your fork:

    ```bash
    git clone git@github.com:<your-username>/waku.git
    cd waku
    ```

2. Install dependencies and pre-commit hooks:

    ```bash
    task deps:install
    ```

3. Verify the setup:

    ```bash
    task -l
    ```

## Making Changes

1. Create a branch from `master`:

    ```bash
    git checkout -b <type>/<description>
    ```

    Branch naming uses the same types as [commits](#commit-conventions): `feat/snapshot-support`, `fix/module-init-order`, `docs/provider-guide`.

2. Make your changes and write tests.

3. Run the full check suite:

    ```bash
    task all
    ```

    For faster iteration on individual files:

    ```bash
    uv run ruff check path/to/file.py
    uv run ruff check --fix path/to/file.py
    uv run ruff format path/to/file.py
    uv run mypy path/to/file.py
    uv run pytest path/to/test_file.py
    ```

4. Commit following the [conventions below](#commit-conventions).

5. Push and open a pull request against `master`:

    ```bash
    git push origin <your-branch>
    ```

## Commit Conventions

Commits are validated by [gitlint](https://jorisroovers.com/gitlint/) via a pre-commit hook.

### Format

```
type(scope): description
```

Scope is optional but encouraged for non-trivial changes.

### Types

| Type | Purpose |
|------|---------|
| `feat` | New feature (minor version bump) |
| `fix` | Bug fix (patch version bump) |
| `perf` | Performance improvement (patch version bump) |
| `docs` | Documentation only |
| `test` | Adding or updating tests |
| `refactor` | Code change that neither fixes a bug nor adds a feature |
| `style` | Formatting, whitespace |
| `build` | Build system or dependency changes |
| `ci` | CI configuration |
| `chore` | Maintenance tasks |

### Scopes

| Scope | Area |
|-------|------|
| `core` | Core framework (`WakuApplication`, `WakuFactory`, modules) |
| `cqrs` | CQRS/Mediator system |
| `di` | Dependency injection helpers |
| `es` | Event sourcing |
| `ext` | Extension system |
| `validation` | Module validation |
| `deps` | Dependency updates |
| `docs` | Documentation tooling |
| `infra` | Infrastructure, deployment |
| `linters` | Linter configuration |
| `release` | Release process |
| `tests` | Test infrastructure |

### Examples

```
feat(cqrs): add retry behavior for pipeline
fix(core): resolve module init order for circular imports
docs: add event sourcing guide
test(ext): cover lifecycle hook edge cases
build(deps): bump dishka to 1.9.0
```

### Breaking Changes

Append `!` after the type/scope:

```
feat(core)!: change module registration API
```

## Code Standards

Pre-commit hooks run on every commit (linting, formatting) and on push (type checking, dependency auditing, security):

- **[Ruff](https://docs.astral.sh/ruff/)** — linting and formatting (`extend-select = ["ALL"]`, single quotes, 120 char line length)
- **[mypy](https://mypy.readthedocs.io/)** — strict type checking
- **[ty](https://github.com/astral-sh/ty)** and **[Pyrefly](https://github.com/facebook/pyrefly)** — additional type checking
- **[Deptry](https://deptry.com/)** — unused/missing dependency detection
- **[Typos](https://github.com/crate-ci/typos)** — spell checking
- **[pysentry-rs](https://github.com/anthropics/pysentry-rs)** — dependency vulnerability scanning

### Key Rules

- Explicit type annotations everywhere
- No relative imports
- Google-style docstrings for public APIs
- `collections.abc` for abstract types, plain `list`/`dict` for concrete
- `Protocol`/`ABC` for interfaces with `IPascalCase` naming (e.g., `IMediator`)

## Testing

Tests use pytest with anyio. Both asyncio and uvloop backends are tested automatically.

```bash
task test           # run tests
task test:cov       # run tests with coverage
```

Coverage minimum: **96%**. Test files mirror the source structure under `tests/`.

## CI Pipeline

Every pull request against `master` triggers:

1. **Change detection** — only relevant checks run based on which files changed
2. **Linting** — ruff check + format verification
3. **Type checking** — mypy, ty, pyrefly
4. **Spell checking** — typos
5. **Tests** — pytest across Python 3.11, 3.12, 3.13, and 3.14
6. **Coverage** — 96% threshold

## Pull Requests

- One feature or fix per PR
- Link related issues (e.g., "Fixes #123")
- Include tests for new functionality
- Update documentation if the change affects public APIs
- `feat` and `fix` commits appear in release notes via [semantic release](https://python-semantic-release.readthedocs.io/) — write commit messages accordingly

## Reporting Issues

Search [existing issues](https://github.com/waku-py/waku/issues) first.

- **Bugs**: Use the [bug report template](https://github.com/waku-py/waku/issues/new?template=bug_report.md) with a minimal reproduction.
- **Features**: Use the [feature request template](https://github.com/waku-py/waku/issues/new?template=feature_request.md) with problem statement and proposed API.

## First-Time Contributors

Look for issues labeled [`good first issue`](https://github.com/waku-py/waku/labels/good-first-issue) or [`help wanted`](https://github.com/waku-py/waku/labels/help-wanted). Comment on the issue to claim it before starting.

## Getting Help

- [GitHub Discussions](https://github.com/waku-py/waku/discussions) — questions and ideas
- [GitHub Issues](https://github.com/waku-py/waku/issues) — bugs and feature requests
