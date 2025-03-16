# Contributing to Waku

First off, thanks for taking the time to contribute! 🎉

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
- [uv](https://docs.astral.sh/uv/getting-started/installation/) - Modern Python package installer
- [Task](https://taskfile.dev/installation/) - Task runner.
  Also, it's recommended to set up [auto-completion](https://taskfile.dev/installation/#setup-completions) for Task.
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

    # If UV is already installed, make sure it's up to date:
    uv self update
    ```

3. Install Task (if not already installed):

    ```bash
    # Using the install script
    sh -c "$(curl --location https://taskfile.dev/install.sh)" -- -d -b /usr/local/bin

    # For other installation options:
    # https://taskfile.dev/installation/
    ```

4. Setup development environment:

    ```bash
    # Install dependencies and setup pre-commit hooks
    task install
    ```

## Development Workflow

### Making Changes

1. Create a new branch for your changes:

    ```bash
    git checkout -b feature/your-feature-name
    ```

2. Make your changes following our [code style guidelines](#code-style)

3. Write tests for your changes

### Testing

Run the test suite before submitting your changes:

```bash
# Run all checks (recommended)
task

# Run specific checks
task test        # Run tests only
task test:cov    # Run tests with coverage
task lint        # Run linters only
task format      # Format code
task typecheck   # Run type checker only
```

### Code Style

We use several tools to maintain code quality:

- [Ruff](https://github.com/astral-sh/ruff) for linting and formatting
- [MyPy](http://mypy-lang.org/) for type checking
- Type hints are required for all public APIs

Key style points:

- Maximum line length is 120 characters
- Use explicit type annotations
- Follow [PEP 8](https://peps.python.org/pep-0008/) guidelines
- Write descriptive
  docstrings ([Google style](https://google.github.io/styleguide/pyguide.html#38-comments-and-docstrings))

## Submitting Changes

### Issues

Before creating an issue:

1. Search existing issues to avoid duplicates
2. Use the appropriate issue template
3. Provide as much context as possible

We welcome:

- Bug reports
- Feature requests
- Documentation improvements
- General questions

### Pull Requests

1. Create an issue first to discuss significant changes
2. Ensure all tests pass and code is formatted
3. Update documentation if needed
4. Follow the pull request template
5. Link related issues in your PR description

#### Pull request checklist:

- [ ] Tests added/updated
- [ ] Documentation updated
- [ ] Type hints added
- [ ] Changelog updated
- [ ] All checks passing

## Development Commands

Common `task` commands:

```bash
task install     # Install dependencies and setup pre-commit hooks
task format      # Format code using ruff
task lint        # Run all linters
task typecheck   # Run type checker (mypy)
task test        # Run tests
task test:cov    # Run tests with coverage
task clean       # Clean build artifacts
task -l          # List all available commands
```

## Questions?

If you have questions, feel free to:

- Open an issue
- Start a [Discussion](https://github.com/waku-py/waku/discussions)
- Reach out to maintainers

Thank you for contributing to Waku! 🙏
