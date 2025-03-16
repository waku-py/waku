# Dependency providers

**Waku** designed to be modular and extensible. To follow this principle, it designed to
be [DI](https://en.wikipedia.org/wiki/Dependency_injection) agnostic framework.

Instead of using a concrete DI framework, **Waku** uses a dependency provider interface. This allows you to
use any DI framework you want, or even write your own.

## Included providers

**Waku** provides several dependency providers for popular DI frameworks out of the box:

### [Aioinject](https://github.com/ThirVondukr/aioinject/)

**Waku** dependency provider interface are heavily inspired by [aioinject](https://github.com/ThirVondukr/aioinject/).
So, it's recommended to use [aioinject](https://github.com/ThirVondukr/aioinject/) as a dependency provider.
It's fits perfectly with **Waku** and provides all needed features.

#### Usage

```python
from waku.di.contrib.aioinject import AioinjectDependencyProvider

dp = AioinjectDependencyProvider()
```

You can also use it with your own container:

```python hl_lines="4"
import aioinject
from waku.di.contrib.aioinject import AioinjectDependencyProvider

container = aioinject.Container()
dp = AioinjectDependencyProvider(container=container)

```

**It supports all aioinject features:**

- All providers scopes (transient, singleton, scoped, object).
- Providers overriding.
- Custom context.
- Extensions (via custom `#!python aioinject.Container` passed to `#!python AioinjectDependencyProvider`)

### [Dishka](https://github.com/ThirVondukr/dishka)

Currently not supported but planned.
