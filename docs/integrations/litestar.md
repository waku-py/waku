---
title: Litestar Integration
hide:
  - toc
---

# Litestar Integration

`waku` can be seamlessly integrated with **Litestar** using the `ApplicationPlugin`. To do this, set up `waku` as usual
and then include the plugin in your Litestar application configuration.

## Example

Here’s how to integrate `waku` with a Litestar application:

```python linenums="1"
from litestar import Litestar
from waku import WakuApplication
from waku.contrib.litestar import WakuPlugin


def bootstrap_application() -> WakuApplication:
    # Replace with your actual waku app setup (e.g., ApplicationFactory.create)
    ...


# Create the waku application
application = bootstrap_application()

# Create the Litestar app with the waku plugin
app = Litestar(plugins=[WakuPlugin(application)])

```

In this example, the `ApplicationPlugin` enables `waku` dependency injection and module system within your Litestar
application.
