---
title: Validation
---

# Validation

## Introduction

Waku validates module configuration at startup to catch wiring errors early, before
your application begins serving requests. Misconfigured imports, missing exports, or
inaccessible dependencies are detected and reported immediately — either as hard
failures or warnings — so you never encounter cryptic runtime resolution errors in
production.

Validation runs as an `AfterApplicationInit` hook, meaning it executes after the
dependency injection container is fully built. At that point, every module, provider,
and import/export relationship is finalized and available for inspection.

## `ValidationExtension`

`ValidationExtension` is the engine that drives startup validation. It is included in
`DEFAULT_EXTENSIONS` and enabled by default — every application created through
`WakuFactory` benefits from validation without any extra configuration.

### Default Setup

```python linenums="1"
from waku.extensions import DEFAULT_EXTENSIONS

# DEFAULT_EXTENSIONS includes:
# ValidationExtension(
#     [DependenciesAccessibleRule()],
#     strict=True,
# )
```

When you create an application with `WakuFactory(AppModule)`, the default extensions
are applied automatically.

### Constructor

```python linenums="1"
from waku.validation import ValidationExtension
from waku.validation.rules import DependenciesAccessibleRule

extension = ValidationExtension(
    rules=[DependenciesAccessibleRule()],
    strict=True,
)
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `rules` | `Sequence[ValidationRule]` | *(required)* | Validation rules to execute |
| `strict` | `bool` | `True` | Fail fast or warn |

### Strict vs. Lenient Mode

**`strict=True`** (default) — raises an `ExceptionGroup` containing all
`ValidationError` instances. The application fails to start.

```python linenums="1"
# Strict mode: application won't start if rules are violated
extension = ValidationExtension(
    rules=[DependenciesAccessibleRule()],
    strict=True,
)
```

**`strict=False`** — emits each violation as a Python warning via `warnings.warn`.
The application starts normally, but violations are logged.

```python linenums="1"
# Lenient mode: log warnings but allow startup
extension = ValidationExtension(
    rules=[DependenciesAccessibleRule()],
    strict=False,
)
```

!!! tip
    Use `strict=False` during migration or prototyping when you want visibility into
    violations without blocking startup. Switch back to `strict=True` before deploying
    to production.

## `DependenciesAccessibleRule`

`DependenciesAccessibleRule` is the built-in rule shipped with waku. It verifies that
every dependency required by every provider is reachable through the module's
import chain.

### What It Checks

For each module, the rule iterates over every provider's factory dependencies and
confirms that each dependency type is **accessible** to the module. Accessibility is
determined through four strategies, checked in order:

1. **Global providers** — the type is provided by a global module (`is_global=True`)
   or registered as an `APP`-scoped context variable.
2. **Local providers** — the type is provided within the same module.
3. **Context variables** — the type is a context variable registered on the module.
4. **Imported modules** — the type is exported by a module that the current module
   imports (direct export or re-export).

If none of these strategies match, the dependency is flagged as inaccessible.

### Example Violation

Consider two modules where `OrderModule` depends on `PaymentService` but does not
import the module that provides it:

```python linenums="1"
from waku.modules import module

@module(providers=[payment_provider], exports=[PaymentService])
class PaymentModule: ...

@module(
    providers=[order_provider],  # order_provider depends on PaymentService
    imports=[],                  # PaymentModule is missing!
)
class OrderModule: ...
```

At startup, the `DependenciesAccessibleRule` detects that `PaymentService` is not
accessible to `OrderModule` and produces a `DependencyInaccessibleError`.

### Error Message Format

The error message provides a clear diagnosis and actionable resolution steps:

```
Dependency Error: "<class 'PaymentService'>" is not accessible
Required by: "<class 'OrderService'>"
In module: "OrderModule"

To resolve this issue, either:
1. Export "<class 'PaymentService'>" from a module that provides it and add that module to "OrderModule" imports
2. Make the module that provides "<class 'PaymentService'>" global by setting is_global=True
3. Move the dependency to a module that has access to "<class 'PaymentService'>"

Note: Dependencies can only be accessed from:
- The same module that provides them
- Modules that import the module that provides and exports it
- Global modules
```

### How to Fix

Choose the approach that best fits your architecture:

| Approach | When to Use |
|----------|-------------|
| Add the missing import and export | The dependency is intentionally scoped to a specific module |
| Set `is_global=True` on the providing module | The dependency is a shared service used across many modules |
| Move the dependency | The provider belongs in a different module closer to its consumers |

```python linenums="1"
# Fix 1: Add the import
@module(
    providers=[order_provider],
    imports=[PaymentModule],  # now OrderModule can access PaymentService
)
class OrderModule: ...

# Fix 2: Make the providing module global
@module(providers=[payment_provider], exports=[PaymentService], is_global=True)
class PaymentModule: ...
```

## Custom Validation Rules

You can implement your own rules to enforce project-specific conventions at startup.

### The `ValidationRule` Protocol

```python linenums="1"
from waku.validation import ValidationRule, ValidationError
from waku.validation._extension import ValidationContext


class ValidationRule(Protocol):
    def validate(self, context: ValidationContext) -> list[ValidationError]: ...
```

The `ValidationContext` provides access to the fully initialized application:

```python linenums="1"
from dataclasses import dataclass
from waku.application import WakuApplication


@dataclass(frozen=True, slots=True, kw_only=True)
class ValidationContext:
    app: WakuApplication  # access container, registry, and all modules
```

Through `context.app`, you can inspect `context.app.registry` (the `ModuleRegistry`
with all registered modules) and `context.app.container` (the Dishka
`AsyncContainer`).

### Example: Custom Rule

The following rule enforces that every non-root module has at least one export,
preventing modules that provide services but forget to expose them:

```python linenums="1"
from waku.validation import ValidationError, ValidationRule
from waku.validation._extension import ValidationContext


class ModulesMustExportRule:
    """Ensure every non-root module exports at least one type."""

    def validate(self, context: ValidationContext) -> list[ValidationError]:
        errors: list[ValidationError] = []
        modules = list(context.app.registry.modules)

        for mod in modules[1:]:  # skip root module
            if not mod.exports:
                errors.append(
                    ValidationError(f'Module "{mod!r}" has providers but no exports')
                )

        return errors
```

### Registering Custom Rules

Pass your custom rules alongside the built-in ones when constructing
`ValidationExtension`:

```python linenums="1"
from waku.validation import ValidationExtension
from waku.validation.rules import DependenciesAccessibleRule

extension = ValidationExtension(
    rules=[
        DependenciesAccessibleRule(),
        ModulesMustExportRule(),
    ],
)
```

Then pass the extension to `WakuFactory`:

```python linenums="1"
from waku.factory import WakuFactory

app = WakuFactory(
    AppModule,
    extensions=[extension],
).create()
```

## Disabling Validation

To disable all default extensions, including validation, pass an empty sequence
to `WakuFactory`:

```python linenums="1"
from waku.factory import WakuFactory

app = WakuFactory(
    AppModule,
    extensions=(),  # no extensions — validation disabled
).create()
```

To disable only `ValidationExtension` while keeping other extensions, construct a
custom extensions list:

```python linenums="1"
from waku.factory import WakuFactory

app = WakuFactory(
    AppModule,
    extensions=[MyOtherExtension()],  # no ValidationExtension
).create()
```

!!! warning
    Disabling validation removes the safety net that catches import/export wiring
    errors at startup. Only disable it when you have a specific reason, such as
    running a minimal test harness or during early prototyping.

## Further reading

- **[Modules](../fundamentals/modules.md)** — module system, imports, and export boundaries
- **[Custom Extensions](../advanced/custom-extensions.md)** — writing your own extensions and hooks
- **[Lifecycle Hooks](lifecycle.md)** — when validation runs in the application lifecycle
- **[Testing](../fundamentals/testing.md)** — test utilities and working with validation in tests
