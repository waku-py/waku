from __future__ import annotations

from waku.di import many, object_
from waku.eventsourcing.contracts.event import IMetadataEnricher
from waku.eventsourcing.forwarding import ForwardingConsumer
from waku.integrations.eventsourcing_messaging.correlation_enricher import CorrelationEnricher
from waku.integrations.eventsourcing_messaging.forwarding_policy import ForwardingPolicy
from waku.integrations.eventsourcing_messaging.session_identity import StoreSessionIdentityExtension
from waku.messaging.pipeline.policy import BehaviorPolicyExtension
from waku.modules._internal.metadata import DynamicModule, module

__all__ = ['EventSourcingMessagingModule']


@module()
class EventSourcingMessagingModule:
    @classmethod
    def register(cls, *, enrich_correlation: bool = True) -> DynamicModule:
        """Register the event-sourcing <-> messaging bridge.

        Auto-registers ``CorrelationEnricher`` into the event-sourcing ``IMetadataEnricher``
        collection, so events appended inside an active message context carry its
        correlation/causation ids in their stored metadata.

        Args:
            enrich_correlation: Pass ``False`` to opt out of the automatic correlation enrichment.
        """
        providers = [object_(ForwardingConsumer(), provided_type=ForwardingConsumer)]
        if enrich_correlation:
            providers.append(many(IMetadataEnricher, CorrelationEnricher, collect=False))
        return DynamicModule(
            parent_module=cls,
            providers=providers,
            extensions=[
                StoreSessionIdentityExtension(),
                BehaviorPolicyExtension(ForwardingPolicy()),
            ],
            is_global=True,
        )
