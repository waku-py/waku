# Contributing

Thank you for considering a contribution to `waku`! 🎉

This guide will help you get started and ensure a smooth process.

## Table of Contents

- [Getting Started](#getting-started)
  - [Prerequisites](#prerequisites)
  - [Development Setup](#development-setup)
- [Development Workflow](#development-workflow)
  - [Making Changes](#making-changes)
  - [Testing](#testing)
  - [Code Style](#code-style)
- [Getting Help](#getting-help)
- [First-time Contributors](#first-time-contributors)
- [Issues](#issues)
- [Project Structure](#project-structure)
- [Commit Message Guidelines](#commit-message-guidelines)

## Getting Started

### Prerequisites

Before you begin, ensure you have the following installed:

- Python 3.11 or higher
- [uv](https://docs.astral.sh/uv/getting-started/installation/) – a modern Python package manager
- [Task](https://taskfile.dev/installation/) – a task runner for automating development workflows (we recommend setting up [auto-completion](https://taskfile.dev/installation/#setup-completions) for Task)
- Git

### Development Setup

1. Fork and clone the repository:

    ```bash
    git clone git@github.com:<your-username>/waku.git
    cd waku
    ```

2. Install UV (if not already installed):

    ```bash
    # On macOS and Linux
    curl -LsSf https://astral.sh/uv/install.sh | sh

    # For other platforms, see:
    # https://docs.astral.sh/uv/getting-started/installation/

    # If uv is already installed, ensure it's up to date:
    uv self update
    ```

3. Install Task (if not already installed):

    ```bash
    # Using the install script
    sh -c "$(curl --location https://taskfile.dev/install.sh)" -- -d -b /usr/local/bin

    # For other installation options, see:
    # https://taskfile.dev/installation/
    ```

4. Set up the development environment:

    ```bash
    # Install dependencies and configure pre-commit hooks
    task install
    ```

    > **Tip:** Run `task -l` after setup to verify everything is working and to see available commands.

## Development Workflow

### Making Changes

1. Fork the repository to your own GitHub account.
2. Clone your fork locally:

    ```bash
    git clone git@github.com:<your-username>/waku.git
    cd waku
    ```

3. Create a new branch for your changes:

    ```bash
    git checkout -b feat/your-feature-name
    ```

4. Make your changes, following our [code style guidelines](#code-style).
5. Write or update tests for your changes.
6. Run all checks and ensure tests pass:

    ```bash
    task
    ```

7. Commit your changes with clear, descriptive messages.
8. Push to your fork:

    ```bash
    git push origin feat/your-feature-name
    ```

9. Open a pull request on GitHub. Link related issues in your PR description (e.g., "Fixes #123").
10. Participate in the review process and make any requested changes.

#### Pull Request Checklist

- [ ] Tests added or updated
- [ ] Documentation updated (if needed)
- [ ] Code is formatted and linted
- [ ] All checks pass
- [ ] Type hints added or refined
- [ ] Commit messages include a detailed description for the changelog

### Testing

Ensure your changes are thoroughly tested by running the following commands:

```bash
# Run all checks (recommended)
task

# Run linters and type checkers
task check

# Run specific checks
task test         # Run tests only
task test:cov     # Run tests with coverage
task lint         # Run linters only
task format       # Format code
task typecheck    # Run type checkers only
```

### Code Style

We use several tools to maintain code quality:

- [Ruff](https://github.com/astral-sh/ruff) for linting and formatting
- [MyPy](http://mypy-lang.org/) and [basedpyright](https://github.com/detachhead/basedpyright) for type checking
- [pre-commit](https://pre-commit.com/) for running checks before commits and pushes

**Key style guidelines:**

- Maximum line length: 120 characters
- Use explicit type annotations throughout the codebase
- Follow [PEP 8](https://peps.python.org/pep-0008/) conventions
- Write descriptive docstrings using the [Google style](https://google.github.io/styleguide/pyguide.html#38-comments-and-docstrings)

## Getting Help

If you have questions or need help, you can:

- Open a [discussion](https://github.com/waku-py/waku/discussions)
- Open an [issue](https://github.com/waku-py/waku/issues) for bugs or feature requests

## First-time Contributors

- Look for issues labeled ["good first issue"](https://github.com/waku-py/waku/labels/good-first-issue) or ["help wanted"](https://github.com/waku-py/waku/labels/help-wanted).
- Comment on the issue to let others know you're working on it.
- Don't hesitate to ask questions if anything is unclear.

## Issues

Before creating an issue:

- Search existing issues to avoid duplicates.
- Use the appropriate issue template for bug reports or feature requests.
- Provide as much context as possible (e.g., steps to reproduce, environment details).

Please follow the [bug report](https://github.com/waku-py/waku/issues/new?template=bug_report.md) and [feature request](https://github.com/waku-py/waku/issues/new?template=feature_request.md) templates when submitting issues.

We welcome:

- Bug reports
- Feature requests
- Documentation improvements
- General questions or ideas

## Project Structure

- `src/` – main source code
- `tests/` – test suite
- `docs/` – documentation
- `Taskfile.yml` – development automation
- `README.md` – project overview

## Commit Message Guidelines

- Use clear, descriptive commit messages.
- Example: `fix(core): handle edge case in dependency resolution`

Thank you for contributing to `waku`! 🙏
