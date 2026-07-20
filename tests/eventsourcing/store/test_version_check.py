from __future__ import annotations

from typing import cast

import pytest

from waku.eventsourcing.contracts.stream import AnyVersion, StreamId
from waku.eventsourcing.store.version_check import check_expected_version


def test_unknown_expected_version_variant_raises() -> None:
    stream_id = StreamId.for_aggregate('Order', 'v-1')

    with pytest.raises(AssertionError):
        check_expected_version(stream_id, cast('AnyVersion', object()), 0, exists=False)
