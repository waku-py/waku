from __future__ import annotations

import pytest
from typing_extensions import override

from waku.di import object_
from waku.messaging._internal.transaction import unit_of_work_scope
from waku.testing import create_test_app
from waku.uow import IUnitOfWork


class _RecordingUoW(IUnitOfWork):
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0

    @override
    async def commit(self) -> None:
        self.commits += 1

    @override
    async def rollback(self) -> None:
        self.rollbacks += 1


async def test_commits_on_clean_exit() -> None:
    uow = _RecordingUoW()
    async with (
        create_test_app(providers=[object_(uow, provided_type=IUnitOfWork)]) as app,
        unit_of_work_scope(app.container) as scope,
    ):
        assert await scope.get(IUnitOfWork) is uow

    assert uow.commits == 1
    assert uow.rollbacks == 0


async def test_rolls_back_on_exception_and_propagates() -> None:
    uow = _RecordingUoW()
    msg = 'boom'
    async with create_test_app(providers=[object_(uow, provided_type=IUnitOfWork)]) as app:
        with pytest.raises(ValueError, match='boom'):
            async with unit_of_work_scope(app.container):
                raise ValueError(msg)

    assert uow.rollbacks == 1
    assert uow.commits == 0
