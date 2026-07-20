from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from typing_extensions import override

from waku.backends.testing import InboxStoreContract

if TYPE_CHECKING:
    from waku._internal.node import INodeRegistry
    from waku.messaging.durability import IDeadLetterStore, IInboxStore

    from tests.messaging.inbox.conftest import InboxBackend

# The exported conformance kit carries the behavioral contract; this suite subscribes the canonical
# fake and the SQLAlchemy adapter, pinning fake == real. SQLAlchemy-only concerns (concurrent
# FOR UPDATE SKIP LOCKED) stay in sqla/.


class TestInboxStoreContract(InboxStoreContract):
    @pytest.fixture
    @override
    def inbox_store(self, inbox_backend: InboxBackend) -> IInboxStore:
        return inbox_backend.inbox

    @pytest.fixture
    @override
    def node_registry(self, inbox_backend: InboxBackend) -> INodeRegistry:
        return inbox_backend.nodes

    @pytest.fixture
    @override
    def dead_letter_store(self, inbox_backend: InboxBackend) -> IDeadLetterStore:
        return inbox_backend.dead_letters
