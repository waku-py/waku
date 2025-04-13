---
title: ASGI Integration
hide:
  - toc
---

# ASGI Integration

`waku` can be seamlessly integrated into any **ASGI** application. To achieve this, set up `waku` as you normally would,
then add the `ApplicationMiddleware` to your ASGI application’s middleware stack.

## Example with FastAPI

```python linenums="1"
from fastapi import FastAPI
from fastapi.middleware import Middleware
from waku import WakuApplication
from waku.contrib.asgi import WakuMiddleware


def bootstrap_application() -> WakuApplication:
    # Replace with your actual waku app setup (e.g., ApplicationFactory.create)
    ...


# Create the waku application
application = bootstrap_application()

# Create the FastAPI app with the waku middleware
app = FastAPI(
    middleware=[
        Middleware(WakuMiddleware, application=application),
    ],
)

```

In this example, the `ApplicationMiddleware` bridges `waku` with FastAPI, allowing dependency injection and module
management within your ASGI routes.
