# waku

<p align="left">
    <sup><i>waku</i> [<b>枠</b>] <i>means framework in Japanese.</i></sup>
    <br/>
</p>

<div align="center">

[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/uv)
[![Python version](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff/)
[![Checked with mypy](http://www.mypy-lang.org/static/mypy_badge.svg)](http://mypy-lang.org/)

[![PyPI](https://img.shields.io/pypi/v/waku.svg)](https://pypi.python.org/pypi/waku)
[![Downloads](https://static.pepy.tech/badge/waku/month)](https://pepy.tech/projects/waku)
[![License](https://img.shields.io/pypi/l/waku.svg)](https://github.com/waku-py/waku/blob/master/LICENSE)

</div>

`waku` *is a microframework for building modular and loosely coupled applications.*

## Overview

`waku` helps you build maintainable Python applications by providing:
- Clean architecture patterns
- Dependency injection
- Module system
- Extension system
- Command/Query handling (CQRS)

## Features

### 🏗️ Modular Architecture
- Build modular monoliths with clear boundaries
- Enforce loose coupling between components
- Validate dependency graphs automatically
- Control module visibility and access

### 🔌 Extensible Plugin System
- Built-in extension mechanism
- Lifecycle hooks for modules and applications
- Custom extension points
- Rich ecosystem of built-in extensions

### 💉 Flexible Dependency Injection
- Framework-agnostic DI implementation
- Providers with different lifetimes (singleton, scoped, transient)
- Easy testing and mocking

### 🎮 Command Query Responsibility Segregation (CQRS)
- Built-in CQRS extension
- Command/Query requests handling
- Event handling
- Middleware support

## Quick Start

### Installation

```sh
# Using pip
pip install waku

# Using UV (recommended)
uv add waku

# Using poetry
poetry add waku
```

### Basic Example

```python
import asyncio

from waku import Application, ApplicationConfig, Module
from waku.di import Scoped, Injected, inject
from waku.di.contrib.aioinject import AioinjectDependencyProvider

# Define your providers
class UserService:
    async def get_user(self, user_id: str) -> dict[str, str]:
        return {"id": user_id, "name": "John Doe"}

# Create a module
user_module = Module(
    name="users",
    providers=[
        Scoped(UserService),
    ],
    exports=[UserService],
)

# Create the application
application = Application(
    name="my_app",
    config=ApplicationConfig(
        dependency_provider=AioinjectDependencyProvider(),
        modules=[user_module],
    ),
)

# Define entrypoints
# In real world this can be FastAPI routes, etc.
@inject
async def handler(user_service: Injected[UserService]) -> dict[str, str]:
    return await user_service.get_user(user_id='123')


# Run the application
# In real world this would be run by a 3rd party framework like FastAPI
async def main() -> None:
    dp = application.dependency_provider
    async with application, dp.context():
        result = await handler()

    print(result)


if __name__ == '__main__':
    asyncio.run(main())
```

## Documentation

For detailed documentation, visit our [documentation site](https://waku-py.github.io/waku/).

### Key Topics
- [Getting Started](https://waku-py.github.io/waku/getting-started)
- [Module System](https://waku-py.github.io/waku/modules)
- [Dependency Injection](https://waku-py.github.io/waku/dependency-injection)
- [Extensions](https://waku-py.github.io/waku/extensions)
- [CQRS](https://waku-py.github.io/waku/cqrs)
- [Integrations](https://waku-py.github.io/waku/integrations)

## Contributing

We welcome contributions! Please see our [Contributing Guide](./CONTRIBUTING.md) for details.

### Development Setup

See out contributing guide for [development setup](./CONTRIBUTING.md#development-setup).

## License

This project is licensed under the [MIT License](./LICENSE).
