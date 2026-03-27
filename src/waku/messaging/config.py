from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Sequence

    from waku.messaging.contracts.pipeline import IPipelineBehavior
    from waku.messaging.endpoints.base import EndpointEntry
    from waku.messaging.router import ModuleRouteDescriptor, RouteDescriptor

__all__ = [
    'MessagingConfig',
]


@dataclass(frozen=True, slots=True, kw_only=True)
class MessagingConfig:
    """Configuration for the messaging extension.

    Attributes:
        pipeline_behaviors: A sequence of pipeline behavior configurations that will be applied
            to the messaging pipeline. Behaviors are executed in the order they are defined.
            Defaults to an empty sequence.
        endpoints: A sequence of endpoint entries defining available message endpoints.
            Defaults to an empty sequence.
        routing: A sequence of route descriptors mapping messages to endpoints.
            Defaults to an empty sequence.

    Example:
        ```python
        config = MessagingConfig(
            pipeline_behaviors=[
                LoggingBehavior,
                ValidationBehavior,
            ]
        )
        ```
    """

    pipeline_behaviors: Sequence[type[IPipelineBehavior[Any, Any]]] = ()
    endpoints: Sequence[EndpointEntry] = ()
    routing: Sequence[RouteDescriptor | ModuleRouteDescriptor] = ()
