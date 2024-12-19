## Contributing

## Issues

Questions, feature requests and bug reports are all welcome as [issues](https://github.com/waku-py/waku/issues/new/choose).

To make it as simple as possible for us to help you, please fill up issue template.

## Pull Requests

Unless your change is trivial (typo, docs tweak etc.), please create an issue to discuss the change before
creating a pull request.

Before making pull request run linters and tests by calling `task` command locally.

**tl;dr**: use `task format` to fix formatting, `task` to run linters and tests.

### Prerequisites

You'll need the following prerequisites:

- Any Python version starting from **Python 3.11**
- [**uv**](https://docs.astral.sh/uv/getting-started/installation/)
- **git**
- **Task**

### Installation and setup

Fork the repository on GitHub and clone your fork locally.

```bash
# Clone your fork and cd into the repo directory
git clone git@github.com:<your username>/waku.git
cd waku

# Install UV
# We use install script
# For other options see:
# https://docs.astral.sh/uv/getting-started/installation/
# On macOS and Linux.
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install Task with install script
# For other options including package managers see:
# https://taskfile.dev/installation/
sh -c "$(curl --location https://taskfile.dev/install.sh)" -- -d -b /usr/local/bin

# Installs waku with all dependencies and extras, setups pre-commit hooks.
task install
```

### Check out a new branch and make your changes

Create a new branch for your changes.

```bash
# Checkout a new branch and make your changes
git checkout -b my-new-feature-branch
# Make your changes...
```

### Run tests and linters

Run tests and linting locally to make sure everything is working as expected.

```bash
# Run automated code formatting and linters
task format
# waku uses ruff for linting and formatting
# (https://github.com/astral-sh/ruff)

# Run tests and linters
task
# There are a few sub-commands in Taskfile like `test`, `test:cov` and `lint`
# which you might want to use, but generally just `task` should be all you need.
# You can run `task -l` to see more options.
```

### Commit and push your changes

Commit your changes, push your branch to GitHub, and create a pull request.
Please follow the pull request template and fill in as much information as possible. Link to any relevant issues and include a description of your changes.
