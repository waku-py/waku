from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from typing_extensions import override

from waku import WakuFactory, module
from waku.messages import IEvent
from waku.messaging import (
    BehaviorPolicyExtension,
    EventHandler,
    IBehaviorPolicy,
    MapFrozenError,
    MessagingConfig,
    MessagingExtension,
    MessagingModule,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from waku.messaging import HandlerMap, HandlerType, PositionedBehavior


@dataclass(frozen=True)
class _Probe(IEvent):
    pass


class _ProbeHandler(EventHandler[_Probe]):
    @override
    async def handle(self, event: _Probe, /) -> None:  # pragma: no cover
        pass


@dataclass(frozen=True)
class _LateBound(IEvent):
    pass


class _LateBoundHandler(EventHandler[_LateBound]):
    @override
    async def handle(self, event: _LateBound, /) -> None:  # pragma: no cover
        pass


class _BindProbePolicy(IBehaviorPolicy):
    def __init__(self) -> None:
        self.outcomes: list[str] = []

    @override
    def behaviors_for(
        self,
        handler: HandlerType,
        handler_map: HandlerMap,
        config: MessagingConfig,
    ) -> Sequence[PositionedBehavior]:
        try:
            handler_map.bind(_LateBound, _LateBoundHandler)
        except MapFrozenError:
            self.outcomes.append('frozen')
        else:
            self.outcomes.append('bound')
        return ()


def test_policy_is_handed_a_frozen_map_that_rejects_late_binds() -> None:
    policy = _BindProbePolicy()

    @module(
        extensions=[
            MessagingExtension().bind(_ProbeHandler),
            BehaviorPolicyExtension(policy),
        ],
    )
    class HandlerModule:
        pass

    @module(imports=[MessagingModule.register(MessagingConfig()), HandlerModule])
    class AppModule:
        pass

    WakuFactory(AppModule).create()

    assert policy.outcomes == ['frozen']
