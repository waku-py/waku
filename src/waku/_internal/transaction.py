from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Generic, TypeAlias, TypeVar

import anyio

from waku.uow import IUnitOfWork

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from dishka import AsyncContainer

__all__ = [
    'Abort',
    'Aborted',
    'Commit',
    'Committed',
    'Rollback',
    'RolledBack',
    'TransactionDecision',
    'TransactionExecution',
    'TransactionExecutionError',
    'TransactionFailureKind',
    'TransactionResult',
    'can_defer_transaction_fatal',
    'execute_in_uow_scope',
    'extract_transaction_execution_error',
]

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


class TransactionExecution:
    __slots__ = ('_executed', '_uow')

    def __init__(self, uow: IUnitOfWork) -> None:
        self._uow = uow
        self._executed = False

    async def execute(
        self,
        operation: Callable[[], Awaitable[TransactionDecision[_CommitT, _RollbackT]]],
    ) -> TransactionResult[_CommitT, _RollbackT]:
        if self._executed:
            msg = 'TransactionExecution can execute only once'
            raise RuntimeError(msg)

        self._executed = True
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
        try:
            await self._uow.commit()
        except BaseException as error:  # noqa: BLE001 - cancellation must also trigger rollback
            return await self._rollback_after_failure(error)
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
        with anyio.CancelScope(shield=True):
            await self._uow.rollback()


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


def can_defer_transaction_fatal(error: BaseException, fatal: TransactionExecutionError) -> bool:
    """Whether a group-wrapped fatal may be held to the end of the current owner unit.

    Deferral is safe only when ``fatal`` surfaced inside a ``BaseExceptionGroup`` whose remainder,
    after splitting out ``TransactionExecutionError``, is empty or an ordinary ``Exception`` group.
    A bare fatal (``fatal is error``) or a remainder still carrying a control-flow ``BaseException``
    must propagate immediately so cancellation is never demoted.
    """
    if fatal is error or not isinstance(error, BaseExceptionGroup):
        return False
    _, remaining = error.split(TransactionExecutionError)
    return remaining is None or isinstance(remaining, Exception)


async def _execute_in_child_scope(
    container: AsyncContainer,
    operation: Callable[[AsyncContainer], Awaitable[TransactionDecision[_CommitT, _RollbackT]]],
) -> TransactionResult[_CommitT, _RollbackT]:
    result: TransactionResult[_CommitT, _RollbackT] | None = None
    execution_error: BaseException | None = None

    async def run_operation(child: AsyncContainer) -> TransactionResult[_CommitT, _RollbackT]:
        nonlocal execution_error
        execution = TransactionExecution(await child.get(IUnitOfWork))

        async def execute_operation() -> TransactionDecision[_CommitT, _RollbackT]:
            return await operation(child)

        try:
            return await execution.execute(execute_operation)
        except BaseException as error:
            execution_error = error
            raise

    try:
        async with container() as child:
            result = await run_operation(child)
    except BaseException as error:
        if isinstance(result, Committed):
            raise TransactionExecutionError(TransactionFailureKind.AFTER_COMMIT, error, None) from error
        # A child-scope teardown failure masks the execution's fatal into __context__, where
        # extract_transaction_execution_error cannot reach it. Re-surface both as group leaves so the
        # fatal stays extractable (and any control-flow leaf stays present) for the owner's split law.
        if (
            execution_error is not None
            and error is not execution_error
            and extract_transaction_execution_error(execution_error) is not None
            and extract_transaction_execution_error(error) is None
        ):
            msg = 'transaction execution failed and child-scope teardown failed'
            raise BaseExceptionGroup(msg, [error, execution_error]) from None
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
