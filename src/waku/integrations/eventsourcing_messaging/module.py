from __future__ import annotations

from waku.integrations.eventsourcing_messaging.forwarding_policy import ForwardingPolicy
from waku.integrations.eventsourcing_messaging.session_identity import StoreSessionIdentityExtension
from waku.messaging.pipeline.policy import BehaviorPolicyExtension
from waku.modules import DynamicModule, module

__all__ = ['EventSourcingMessagingModule']


@module()
class EventSourcingMessagingModule:
    @classmethod
    def register(cls) -> DynamicModule:
        return DynamicModule(
            parent_module=cls,
            extensions=[
                StoreSessionIdentityExtension(),
                BehaviorPolicyExtension(ForwardingPolicy()),
            ],
            is_global=True,
        )
