# waku

<p align="center" markdown="1">
    <sup><i>waku</i> [<b>枠</b> or <b>わく</b>] <i>means framework in Japanese.</i></sup>
    <br/>
</p>

-----

<div align="center" markdown="1">

[![CI/CD](https://img.shields.io/github/actions/workflow/status/waku-py/waku/release.yml?branch=master&logo=github&label=CI/CD)](https://github.com/waku-py/waku/actions?query=event%3Apush+branch%3Amaster+workflow%3ACI/CD)
[![Downloads](https://static.pepy.tech/badge/waku/month)](https://pepy.tech/projects/waku)
[![PyPI](https://img.shields.io/pypi/v/waku.svg?label=PyPI)](https://pypi.python.org/pypi/waku)
[![Python version](https://img.shields.io/pypi/pyversions/waku.svg?label=Python)](https://www.python.org/downloads/)
[![License](https://img.shields.io/pypi/l/waku.svg)](https://github.com/waku-py/waku/blob/master/LICENSE)
[![Docs](https://img.shields.io/badge/docs-mkdocs%20material-blue)](https://waku-py.github.io/waku/)

[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/uv)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff/)
[![mypy - checked](http://www.mypy-lang.org/static/mypy_badge.svg)](http://mypy-lang.org/)
[![basedpyright - checked](https://img.shields.io/badge/basedpyright-checked-42b983?color=ffc105)](https://docs.basedpyright.com)

</div>

---

> **waku** is a modular, type-safe Python framework for scalable, maintainable applications.
> Inspired by NestJS, powered by [Dishka](https://github.com/reagento/dishka/) IoC.

## Why `waku`?

- **🧩 Modular by design:** Enforces clear boundaries and single responsibility.
- **💉 First-class Dependency Injection:** Powered by [Dishka](https://github.com/reagento/dishka/).
- **⚡ Event-driven and extensible:** Built-in hooks, CQRS, and plugin system.
- **🔌 Framework-agnostic:** Integrates with FastAPI, Litestar, FastStream, Aiogram, and more.
- **🛡️ Production-ready:** Type-safe, testable, and maintainable.

## Who is it for?

- Teams building enterprise or microservice Python apps
- Developers seeking a clean, maintainable architecture
- Projects that require testability, loose coupling, and clear module boundaries

## Features

- 🧩 [**Modular architecture**](https://waku-py.github.io/waku/usage/modules/): Build applications as a set of loosely coupled modules with clear boundaries, automatic dependency validation, and controlled visibility.
- 💉 [**Dependency Injection**](https://waku-py.github.io/waku/usage/providers/): Use [Dishka](https://github.com/reagento/dishka/)'s IoC container for type-safe, testable, and maintainable dependency management.
- 📨 [**CQRS/Mediator**](https://waku-py.github.io/waku/usage/mediator/): Handle commands, queries, and events with a mediator abstraction inspired by C# [MediatR](https://github.com/jbogard/MediatR).
- 🧰 [**Extensions & plugins**](https://waku-py.github.io/waku/usage/extensions/): Extend `waku` with custom plugins that can hook into the application lifecycle, add providers, or integrate with external systems.
- 🔄 [**Lifespan management**](https://waku-py.github.io/waku/usage/lifespan/): Automatically manage application and IoC container lifecycles with built-in hooks and an event system.
- 🤝 [**Integrations**](https://waku-py.github.io/waku/integrations/): Out-of-the-box support for FastAPI, Litestar, FastStream, Aiogram, and more, leveraging Dishka's integrations.

## Quick Start

### Installation

```sh
uv add waku
# or
pip install waku
```

### Minimal Example

```python
import asyncio

from waku import WakuFactory, module
from waku.di import scoped


class GreetingService:
    async def greet(self, name: str) -> str:
        return f'Hello, {name}!'


@module(providers=[scoped(GreetingService)])
class GreetingModule:
    pass


@module(imports=[GreetingModule])
class AppModule:
    pass


async def main() -> None:
    app = WakuFactory(AppModule).create()

    async with app, app.container() as c:
        svc = await c.get(GreetingService)
        print(await svc.greet('waku'))


if __name__ == '__main__':
    asyncio.run(main())

```

For more realistic examples, see the [Getting Started](https://waku-py.github.io/waku/getting-started) guide.

## Documentation

- [Getting Started](https://waku-py.github.io/waku/getting-started/)
- [Module System](https://waku-py.github.io/waku/usage/modules/)
- [Providers](https://waku-py.github.io/waku/usage/providers/)
- [Extensions](https://waku-py.github.io/waku/usage/extensions/)
- [Mediator (CQRS)](https://waku-py.github.io/waku/usage/mediator/)
- [API Reference](https://waku-py.github.io/waku/reference/)
- [Dishka Documentation](https://dishka.readthedocs.io/en/stable/index.html/)

## Contributing

- [Contributing Guide](https://waku-py.github.io/waku/contributing/)
- [Development Setup](https://waku-py.github.io/waku/contributing/#development-setup)

## Roadmap

- [ ] Improve inner architecture
- [ ] Improve documentation
- [ ] Add new and improve existing validation rules
- [ ] Provide example projects for common architectures

## Support

- [GitHub Issues](https://github.com/waku-py/waku/issues)
- [Discussions](https://github.com/waku-py/waku/discussions)

## License

MIT
