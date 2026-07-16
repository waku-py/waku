from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Generic, TypeAlias, TypeVar

import anyio

from waku.uow import IUnitOfWork

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Awaitable, Callable

    from dishka import AsyncContainer

__all__ = [
    'Abort',
    'Aborted',
    'Commit',
    'Committed',
    'Rollback',
    'RolledBack',
    'TransactionCleanupError',
    'TransactionDecision',
    'TransactionExecution',
    'TransactionExecutionError',
    'TransactionFailureKind',
    'TransactionResult',
    'commit_uow',
    'execute_in_uow_scope',
    'extract_transaction_execution_error',
    'rollback_uow',
    'transaction_scope',
    'unit_of_work_scope',
]

logger = logging.getLogger(__name__)

_CommitT = TypeVar('_CommitT')
_CommitT_co = TypeVar('_CommitT_co', covariant=True)
_RollbackT = TypeVar('_RollbackT')
_RollbackT_co = TypeVar('_RollbackT_co', covariant=True)


@dataclass(frozen=True, slots=True)
class Commit(Generic[_CommitT_co]):
    value: _CommitT_co


@dataclass(frozen=True, slots=True)
class Rollback(Generic[_RollbackT_co]):
    value: _RollbackT_co


@dataclass(frozen=True, slots=True)
class Abort:
    error: Exception


@dataclass(frozen=True, slots=True)
class Committed(Generic[_CommitT_co]):
    value: _CommitT_co


@dataclass(frozen=True, slots=True)
class RolledBack(Generic[_RollbackT_co]):
    value: _RollbackT_co


@dataclass(frozen=True, slots=True)
class Aborted:
    error: Exception


TransactionDecision: TypeAlias = Commit[_CommitT] | Rollback[_RollbackT] | Abort
TransactionResult: TypeAlias = Committed[_CommitT] | RolledBack[_RollbackT] | Aborted


class TransactionFailureKind(Enum):
    ROLLBACK_FAILED = 'ROLLBACK_FAILED'
    AFTER_COMMIT = 'AFTER_COMMIT'


class TransactionExecutionError(BaseException):
    __slots__ = ('error', 'kind', 'primary_error')

    def __init__(
        self,
        kind: TransactionFailureKind,
        error: BaseException,
        primary_error: BaseException | None,
    ) -> None:
        super().__init__(f'Transaction execution failed: {kind.value}')
        self.kind = kind
        self.error = error
        self.primary_error = primary_error


class _ExecutionState(Enum):
    READY = 'READY'
    RUNNING = 'RUNNING'
    COMMITTING = 'COMMITTING'
    COMMITTED = 'COMMITTED'
    ROLLING_BACK = 'ROLLING_BACK'
    ROLLED_BACK = 'ROLLED_BACK'
    FAILED = 'FAILED'


class TransactionExecution:
    __slots__ = ('_state', '_uow')

    def __init__(self, uow: IUnitOfWork) -> None:
        self._uow = uow
        self._state = _ExecutionState.READY

    async def execute(
        self,
        operation: Callable[[], Awaitable[TransactionDecision[_CommitT, _RollbackT]]],
    ) -> TransactionResult[_CommitT, _RollbackT]:
        if self._state is not _ExecutionState.READY:
            msg = 'TransactionExecution can execute only once'
            raise RuntimeError(msg)

        self._state = _ExecutionState.RUNNING
        try:
            decision = await operation()
        except BaseException as error:  # noqa: BLE001 - control flow must also trigger rollback
            return await self._rollback_after_failure(error)

        if isinstance(decision, Commit):
            return await self._commit(decision.value)
        if isinstance(decision, Rollback):
            return await self._rollback(decision.value)
        return await self._rollback_after_failure(decision.error)

    async def _commit(self, value: _CommitT) -> Committed[_CommitT] | Aborted:
        self._state = _ExecutionState.COMMITTING
        try:
            await self._uow.commit()
        except BaseException as error:  # noqa: BLE001 - cancellation must also trigger rollback
            return await self._rollback_after_failure(error)
        self._state = _ExecutionState.COMMITTED
        return Committed(value)

    async def _rollback(self, value: _RollbackT) -> RolledBack[_RollbackT]:
        try:
            await self._run_rollback()
        except BaseException as rollback_error:
            raise TransactionExecutionError(
                TransactionFailureKind.ROLLBACK_FAILED,
                rollback_error,
                None,
            ) from rollback_error
        return RolledBack(value)

    async def _rollback_after_failure(self, primary_error: BaseException) -> Aborted:
        try:
            await self._run_rollback()
        except BaseException as rollback_error:
            fatal = TransactionExecutionError(
                TransactionFailureKind.ROLLBACK_FAILED,
                rollback_error,
                primary_error,
            )
            if not _has_control_flow_leaf(primary_error):
                raise fatal from rollback_error
            fatal.__cause__ = rollback_error
            raise primary_error from fatal

        if extract_transaction_execution_error(primary_error) is not None:
            raise primary_error
        if isinstance(primary_error, Exception):
            return Aborted(primary_error)
        raise primary_error

    async def _run_rollback(self) -> None:
        self._state = _ExecutionState.ROLLING_BACK
        try:
            with anyio.CancelScope(shield=True):
                await self._uow.rollback()
        except BaseException:
            self._state = _ExecutionState.FAILED
            raise
        self._state = _ExecutionState.ROLLED_BACK


def _has_control_flow_leaf(error: BaseException) -> bool:
    if isinstance(error, BaseExceptionGroup):
        return any(_has_control_flow_leaf(nested) for nested in error.exceptions)
    return not isinstance(error, TransactionExecutionError | Exception)


def extract_transaction_execution_error(error: BaseException) -> TransactionExecutionError | None:
    if isinstance(error, TransactionExecutionError):
        return error
    if isinstance(error, BaseExceptionGroup):
        for nested in error.exceptions:
            fatal = extract_transaction_execution_error(nested)
            if fatal is not None:
                return fatal
    return None


async def _execute_in_child_scope(
    container: AsyncContainer,
    operation: Callable[[AsyncContainer], Awaitable[TransactionDecision[_CommitT, _RollbackT]]],
) -> TransactionResult[_CommitT, _RollbackT]:
    result: TransactionResult[_CommitT, _RollbackT] | None = None
    try:
        async with container() as child:
            uow = await child.get(IUnitOfWork)
            execution = TransactionExecution(uow)

            async def execute_operation() -> TransactionDecision[_CommitT, _RollbackT]:
                return await operation(child)

            result = await execution.execute(execute_operation)
    except BaseException as error:
        if isinstance(result, Committed):
            raise TransactionExecutionError(TransactionFailureKind.AFTER_COMMIT, error, None) from error
        raise

    return result


async def execute_in_uow_scope(
    container: AsyncContainer,
    operation: Callable[[AsyncContainer], Awaitable[TransactionDecision[_CommitT, _RollbackT]]],
    *,
    after_commit: Callable[[_CommitT], Awaitable[None]] | None = None,
) -> TransactionResult[_CommitT, _RollbackT]:
    result = await _execute_in_child_scope(container, operation)
    if isinstance(result, Committed) and after_commit is not None:
        try:
            await after_commit(result.value)
        except BaseException as error:
            raise TransactionExecutionError(TransactionFailureKind.AFTER_COMMIT, error, None) from error
    return result


class TransactionCleanupError(Exception):
    """Carry a failed rollback across a boundary that may absorb the primary error."""

    __slots__ = ('primary_error', 'rollback_error')

    def __init__(self, primary_error: Exception | None, rollback_error: BaseException) -> None:
        super().__init__('Rollback failed before the transaction owner could produce an outcome')
        self.primary_error = primary_error
        self.rollback_error = rollback_error


async def rollback_uow(
    uow: IUnitOfWork,
    *,
    primary_error: BaseException | None = None,
    rollback_failure_is_primary: bool = False,
) -> None:
    try:
        with anyio.CancelScope(shield=True):
            await uow.rollback()
    except BaseException as rollback_error:
        signal_cleanup_failure = rollback_failure_is_primary and (
            primary_error is None or isinstance(primary_error, Exception)
        )
        if signal_cleanup_failure:
            raise TransactionCleanupError(
                primary_error if isinstance(primary_error, Exception) else None,
                rollback_error,
            ) from rollback_error
        if primary_error is None:
            raise
        logger.exception('Rollback failed while preserving the primary transaction failure')


async def commit_uow(uow: IUnitOfWork, *, rollback_failure_is_primary: bool = False) -> None:
    try:
        await uow.commit()
    except BaseException as exc:
        await rollback_uow(
            uow,
            primary_error=exc,
            rollback_failure_is_primary=rollback_failure_is_primary,
        )
        raise


@asynccontextmanager
async def transaction_scope(
    uow: IUnitOfWork,
    *,
    rollback_failure_is_primary: bool = False,
) -> AsyncGenerator[None]:
    try:
        yield
    except BaseException as exc:
        await rollback_uow(
            uow,
            primary_error=exc,
            rollback_failure_is_primary=rollback_failure_is_primary,
        )
        raise
    else:
        await commit_uow(uow, rollback_failure_is_primary=rollback_failure_is_primary)


@asynccontextmanager
async def unit_of_work_scope(
    container: AsyncContainer,
    *,
    rollback_failure_is_primary: bool = False,
) -> AsyncGenerator[AsyncContainer]:
    async with container() as scope:
        uow = await scope.get(IUnitOfWork)
        async with transaction_scope(uow, rollback_failure_is_primary=rollback_failure_is_primary):
            yield scope
