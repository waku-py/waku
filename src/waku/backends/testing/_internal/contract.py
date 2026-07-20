from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from waku.modules._internal.metadata import DynamicModule

__all__ = ['BackendContract']


class BackendContract:
    """Shared conformance-contract base: the ``backend_module`` seam each backend suite overrides.

    Concrete contracts (``BackendAssemblyContract``, ``SequenceAllocatorContract``) subclass this and
    inherit the abstract ``backend_module`` fixture; a backend's test suite overrides it to return the
    registered backend (plus any resource setup/teardown around the yield).
    """

    @pytest.fixture
    def backend_module(self) -> DynamicModule:
        msg = 'override the backend_module fixture with your registered backend'
        raise NotImplementedError(msg)  # pragma: no cover
