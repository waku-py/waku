from __future__ import annotations

import abc
from typing import TYPE_CHECKING

import pytest
from typing_extensions import override

from waku.eventsourcing.projection.interfaces import IProjection

if TYPE_CHECKING:
    from collections.abc import Sequence

    from waku.eventsourcing.contracts.event import StoredEvent


def test_concrete_projection_without_name_raises_type_error() -> None:
    with pytest.raises(TypeError, match='must define projection_name'):

        class BadProjection(IProjection):
            @override
            async def project(self, events: Sequence[StoredEvent], /) -> None: ...


def test_concrete_projection_with_empty_name_raises_type_error() -> None:
    with pytest.raises(TypeError, match='must define projection_name'):

        class BadProjection(IProjection):
            projection_name = ''

            @override
            async def project(self, events: Sequence[StoredEvent], /) -> None: ...


def test_concrete_projection_with_name_succeeds() -> None:
    class GoodProjection(IProjection):
        projection_name = 'good_projection'

        @override
        async def project(self, events: Sequence[StoredEvent], /) -> None: ...

    assert GoodProjection.projection_name == 'good_projection'


def test_abstract_subclass_without_name_is_allowed() -> None:
    class AbstractBaseProjection(IProjection, abc.ABC):
        @abc.abstractmethod
        def some_helper(self) -> None: ...
