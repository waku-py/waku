from __future__ import annotations

import abc
from typing import TYPE_CHECKING

import pytest
from typing_extensions import override

from waku.eventsourcing.projection.interfaces import ErrorPolicy, ICatchUpProjection, IProjection

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


# --- ICatchUpProjection ---


def test_catch_up_projection_without_name_raises_type_error() -> None:
    with pytest.raises(TypeError, match='must define projection_name'):

        class BadCatchUp(ICatchUpProjection):
            @override
            async def project(self, events: Sequence[StoredEvent], /) -> None: ...


def test_catch_up_projection_with_name_succeeds() -> None:
    class GoodCatchUp(ICatchUpProjection):
        projection_name = 'good_catch_up'

        @override
        async def project(self, events: Sequence[StoredEvent], /) -> None: ...

    assert GoodCatchUp.projection_name == 'good_catch_up'


def test_catch_up_projection_default_error_policy_is_retry() -> None:
    class MyCatchUp(ICatchUpProjection):
        projection_name = 'my_catch_up'

        @override
        async def project(self, events: Sequence[StoredEvent], /) -> None: ...

    assert MyCatchUp.error_policy is ErrorPolicy.RETRY


async def test_catch_up_projection_default_teardown_is_noop() -> None:
    class MyCatchUp(ICatchUpProjection):
        projection_name = 'my_catch_up'

        @override
        async def project(self, events: Sequence[StoredEvent], /) -> None: ...

    projection = MyCatchUp()
    await projection.teardown()


def test_abstract_catch_up_subclass_without_name_is_allowed() -> None:
    class AbstractCatchUp(ICatchUpProjection, abc.ABC):
        @abc.abstractmethod
        def some_helper(self) -> None: ...
