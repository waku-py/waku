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
- [Submitting Changes](#submitting-changes)
  - [Issues](#issues)
  - [Pull Requests](#pull-requests)
- [Development Commands](#development-commands)

## Getting Started

### Prerequisites

Before you begin, ensure you have the following installed:

- Python 3.11 or higher
- [uv](https://docs.astral.sh/uv/getting-started/installation/) - A modern Python package manager
- [Task](https://taskfile.dev/installation/) - A task runner for automating development workflows.
  We recommend setting up [auto-completion](https://taskfile.dev/installation/#setup-completions) for Task.
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

    # If uv is already installed, ensure it’s up to date:
    uv self update
    ```

3. Install Task (if not already installed):

    ```bash
    # Using the install script
    sh -c "$(curl --location https://taskfile.dev/install.sh)" -- -d -b /usr/local/bin

    # For other installation options, see:
    # https://taskfile.dev/installation/
    ```

4. Setup development environment:

    ```bash
    # Install dependencies and configure pre-commit hooks
    task install
    ```

    > **Tip:** Run `task -l` after setup to verify everything is working and see available commands.

## Development Workflow

### Making Changes

1. Create a new branch for your changes:

    ```bash
    git checkout -b feature/your-feature-name
    ```

2. Make your changes following our [code style guidelines](#code-style)

3. Write tests for your changes

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

## Submitting Changes

### Issues

Before creating an issue:

1. Search existing issues to avoid duplicates.
2. Use the appropriate issue template.
3. Provide as much context as possible (e.g., steps to reproduce, environment details).

We welcome:

- Bug reports
- Feature requests
- Documentation improvements
- General questions or ideas

### Pull Requests

1. Discuss significant changes by creating an issue first.
2. Ensure all tests pass and code is formatted.
3. Update documentation if your changes affect it.
4. Follow the pull request template.
5. Link related issues in your PR description (e.g., "Fixes #123").

**Pull request checklist:**

- [ ] Tests added or updated
- [ ] Documentation updated (if applicable)
- [ ] Type hints added or refined
- [ ] Commit messages include a detailed description for the changelog
- [ ] All checks pass

## Development Commands

Use these common `task` commands during development:

```bash
task install     # Install dependencies and set up pre-commit hooks
task format      # Format code using Ruff
task lint        # Run all linters
task typecheck   # Run type checkers (MyPy and basedpyright)
task test        # Run tests
task test:cov    # Run tests with coverage
task clean       # Clean build artifacts
task -l          # List all available commands
```

## Questions?

Need help? Feel free to:

- Open an [issue](https://github.com/waku-py/waku/issues)
- Start a [discussion](https://github.com/waku-py/waku/discussions)
- Contact the maintainers directly

Thank you for contributing to `waku`! 🙏
