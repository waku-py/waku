from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import IntEnum
from typing import TYPE_CHECKING, Any

from typing_extensions import override

from waku.extensions import OnModuleConfigure

if TYPE_CHECKING:
    from collections.abc import Sequence

    from waku.messaging.config import MessagingConfig
    from waku.messaging.contracts.handler import HandlerType
    from waku.messaging.contracts.pipeline import IPipelineBehavior
    from waku.messaging.registry import MessageRegistry
    from waku.modules import ModuleMetadata

__all__ = [
    'BehaviorPlan',
    'BehaviorPolicyExtension',
    'IBehaviorPolicy',
    'Position',
    'PositionedBehavior',
    'build_behavior_plan',
]


class Position(IntEnum):
    """Pipeline rank: LOWER = OUTERMOST (wraps first, runs at executor.step(0))."""

    CASCADE_FRAME = 100
    USER_GLOBAL = 200
    OUTBOX_DRAIN = 300
    HANDLER_LOCAL = 400
    FORWARDING = 500


@dataclass(frozen=True, slots=True)
class PositionedBehavior:
    behavior: type[IPipelineBehavior[Any, Any]]
    position: Position
    sequence: int = field(default=0)

    @property
    def sort_key(self) -> tuple[int, int]:
        return (int(self.position), self.sequence)


class IBehaviorPolicy(ABC):
    @abstractmethod
    def behaviors_for(
        self,
        handler: HandlerType,
        registry: MessageRegistry,
        config: MessagingConfig,
    ) -> Sequence[PositionedBehavior]: ...


@dataclass(frozen=True, slots=True)
class BehaviorPolicyExtension(OnModuleConfigure):
    """Module-extension seam contributing an IBehaviorPolicy into the pipeline assembly.

    Declare it in a module's ``extensions=[...]``; ``MessageRegistryAggregator`` discovers it via
    ``find_extensions`` and folds the policy into every handler's ``BehaviorPlan``. One mechanism for
    framework, event-sourcing (event forwarding), and user-supplied policies alike.

    It subclasses ``OnModuleConfigure`` solely to qualify as a discoverable ``ModuleExtension``; the
    hook itself is a deliberate no-op (this extension only carries data, it runs no configure logic).
    """

    policy: IBehaviorPolicy

    @override
    def on_module_configure(self, metadata: ModuleMetadata) -> None:
        """No-op marker: discovered passively via find_extensions, not at configure time."""


@dataclass(frozen=True, slots=True)
class BehaviorPlan:
    """Memoized per-handler resolved behavior chain (outermost-first), built at registration."""

    _plan: dict[HandlerType, tuple[type[IPipelineBehavior[Any, Any]], ...]]

    def for_handler(self, handler: HandlerType) -> tuple[type[IPipelineBehavior[Any, Any]], ...]:
        return self._plan.get(handler, ())


def build_behavior_plan(
    handlers: Sequence[HandlerType],
    policies: Sequence[IBehaviorPolicy],
    registry: MessageRegistry,
    config: MessagingConfig,
) -> BehaviorPlan:
    plan: dict[HandlerType, tuple[type[IPipelineBehavior[Any, Any]], ...]] = {}
    for handler in handlers:
        positioned: list[PositionedBehavior] = []
        for policy in policies:
            positioned.extend(policy.behaviors_for(handler, registry, config))
        positioned.sort(key=lambda item: item.sort_key)
        plan[handler] = tuple(item.behavior for item in positioned)
    return BehaviorPlan(plan)
