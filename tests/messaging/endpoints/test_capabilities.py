import importlib
import pkgutil

import pytest

import waku.messaging.endpoints._internal as endpoints_internal
from waku.messaging.endpoints._internal.durable_local_queue import DurableLocalQueueEndpoint
from waku.messaging.endpoints._internal.external import ExternalEndpoint
from waku.messaging.endpoints._internal.inline import InlineEndpoint
from waku.messaging.endpoints._internal.local_queue import LocalQueueEndpoint
from waku.messaging.endpoints.base import Endpoint

# The routing contract these flags govern: is_outbox_backed drives _split_by_durability (committed-owner
# routing + cascade atomicity) and supports_scheduling drives _reject_unschedulable (fail-loud on a
# scheduled message to an endpoint that cannot persist it). (is_outbox_backed, supports_scheduling) per type.
CAPABILITY_CONTRACT: dict[type[Endpoint], tuple[bool, bool]] = {
    ExternalEndpoint: (True, False),
    DurableLocalQueueEndpoint: (False, True),
    LocalQueueEndpoint: (False, False),
    InlineEndpoint: (False, False),
}


@pytest.mark.parametrize(
    ('endpoint_type', 'flags'),
    [pytest.param(cls, flags, id=cls.__name__) for cls, flags in CAPABILITY_CONTRACT.items()],
)
def test_endpoint_capability_flags(endpoint_type: type[Endpoint], flags: tuple[bool, bool]) -> None:
    is_outbox_backed, supports_scheduling = flags
    assert endpoint_type.is_outbox_backed is is_outbox_backed
    assert endpoint_type.supports_scheduling is supports_scheduling


def test_capability_contract_covers_every_production_endpoint() -> None:
    # A new production endpoint that forgets to pin its capabilities turns THIS red instead of silently
    # routing as non-outbox/non-scheduling (lost atomicity, no fail-loud). Import every endpoint module
    # so __subclasses__ sees the full production set, then compare against the pinned contract.
    for module in pkgutil.iter_modules(endpoints_internal.__path__, f'{endpoints_internal.__name__}.'):
        importlib.import_module(module.name)
    production = {
        cls
        for cls in _all_subclasses(Endpoint)
        if not getattr(cls, '__abstractmethods__', None) and cls.__module__.startswith('waku.messaging.endpoints')
    }
    assert production == set(CAPABILITY_CONTRACT)


def _all_subclasses(cls: type) -> set[type]:
    subclasses = set(cls.__subclasses__())
    return subclasses.union(*(_all_subclasses(subclass) for subclass in subclasses))
