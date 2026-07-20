from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from typing_extensions import override

from waku.backends.testing import OutboxStoreContract

if TYPE_CHECKING:
    from waku._internal.node import INodeRegistry
    from waku.messaging.durability import IDeadLetterStore, IOutboxStore

    from tests.messaging.outbox.conftest import OutboxBackend

# The exported conformance kit carries the behavioral contract; this suite subscribes the memory
# backend's faithful store and the SQLAlchemy adapter, pinning fake == real. SQLAlchemy-only
# concerns (concurrent FOR UPDATE SKIP LOCKED) stay in sqla/.


class TestOutboxStoreContract(OutboxStoreContract):
    @pytest.fixture
    @override
    def outbox_store(self, outbox_backend: OutboxBackend) -> IOutboxStore:
        return outbox_backend.outbox

    @pytest.fixture
    @override
    def node_registry(self, outbox_backend: OutboxBackend) -> INodeRegistry:
        return outbox_backend.nodes

    @pytest.fixture
    @override
    def dead_letter_store(self, outbox_backend: OutboxBackend) -> IDeadLetterStore:
        return outbox_backend.dead_letters
