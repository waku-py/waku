from __future__ import annotations

from waku.messaging.config import MessagingConfig, OutboxConfig
from waku.messaging.endpoints.base import external_endpoint
from waku.messaging.endpoints.merge import merge_broker_endpoints
from waku.messaging.errors import RetryAction
from waku.messaging.modules import _build_sending_failure_registry  # noqa: PLC2701
from waku.messaging.outbox.interfaces import IOutboxStore
from waku.messaging.sending.policy import SendingFailurePolicy


def _outbox_config() -> OutboxConfig:
    return OutboxConfig(store=IOutboxStore)


def test_per_endpoint_policy_is_registered_by_destination() -> None:
    policy = SendingFailurePolicy.on_exception(ConnectionError).discard()
    config = MessagingConfig(
        endpoints=(external_endpoint('amqp://orders', sending_failure_policies=[policy]),),
        outbox=_outbox_config(),
    )
    merged = merge_broker_endpoints(config.endpoints, inbox_configured=False)
    registry = _build_sending_failure_registry(merged, config)
    resolved = registry.resolve('amqp://orders', ConnectionError())
    assert resolved is not None
    assert resolved.stages[0].action is RetryAction.DISCARD


def test_unmatched_destination_falls_back_to_synthesized_catch_all() -> None:
    config = MessagingConfig(endpoints=(external_endpoint('amqp://orders'),), outbox=_outbox_config())
    merged = merge_broker_endpoints(config.endpoints, inbox_configured=False)
    registry = _build_sending_failure_registry(merged, config)
    resolved = registry.resolve('amqp://orders', RuntimeError())
    # synthesized relay default: retry-with-backoff then dead-letter
    assert resolved is not None
    assert [s.action for s in resolved.stages] == [RetryAction.RETRY_WITH_BACKOFF, RetryAction.DEAD_LETTER]


def test_per_endpoint_policy_shadows_synthesized_catch_all() -> None:
    # When an endpoint declares its own on_any_exception policy, an endpoint's own policy wins (it is
    # keyed per-destination; the synthesized catch-all is a default-tier on_any_exception, lower
    # specificity).
    policy = SendingFailurePolicy.on_any_exception().discard()
    config = MessagingConfig(
        endpoints=(external_endpoint('amqp://orders', sending_failure_policies=[policy]),),
        outbox=_outbox_config(),
    )
    merged = merge_broker_endpoints(config.endpoints, inbox_configured=False)
    registry = _build_sending_failure_registry(merged, config)
    resolved = registry.resolve('amqp://orders', RuntimeError())
    assert resolved is not None
    assert resolved.stages[0].action is RetryAction.DISCARD


def test_no_outbox_means_no_synthesized_default() -> None:
    config = MessagingConfig()
    merged = merge_broker_endpoints(config.endpoints, inbox_configured=False)
    registry = _build_sending_failure_registry(merged, config)
    assert registry.resolve('amqp://orders', RuntimeError()) is None
