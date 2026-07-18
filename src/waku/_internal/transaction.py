from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Generic, Never, TypeAlias, TypeVar, assert_never

import anyio

from waku.uow import IUnitOfWork

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from dishka import AsyncContainer

__all__ = [
    'Abort',
    'Aborted',
    'AfterCommitError',
    'Commit',
    'Committed',
    'Rollback',
    'RollbackFailedError',
    'RolledBack',
    'TransactionDecision',
    'TransactionExecution',
    'TransactionExecutionError',
    'TransactionResult',
    'can_defer_transaction_fatal',
    'execute_in_uow_scope',
    'extract_transaction_execution_error',
    'require_committed',
    'reraise_transaction_fatal',
    'run_committed',
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
    """Owner-detected forced rollback with no exception in flight — the Spring-strict rollback-only seam.

    Sole consumer: ``TransactionalBehavior``'s outer frame, where ``call_next`` returned normally but a
    nested frame recorded a swallowed failure. Carrying the pre-built error as data (not ``raise``) keeps
    it out of the ``except`` round-trip so the interpreter never grafts a ``__context__`` onto it — the
    no-causal-mutation law — and completes the decision union's totality against ``Aborted``.
    """

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


class TransactionExecutionError(BaseException):
    """Base for the two fatal transaction signals.

    Typed ``BaseException`` (not ``Exception``) so ordinary ``except Exception`` owner boundaries skip it
    and propagate it unchanged, exactly like cancellation. Concrete role is carried by the subclass.
    """

    __slots__ = ('error', 'primary_error')

    def __init__(self, error: BaseException, primary_error: BaseException | None = None) -> None:
        super().__init__(f'Transaction execution failed: {type(self).__name__}')
        self.error = error
        self.primary_error = primary_error


class RollbackFailedError(TransactionExecutionError):
    """A required rollback itself failed — the one fatal with no normal owner result."""

    __slots__ = ()


class AfterCommitError(TransactionExecutionError):
    """A failure surfaced after the transaction already committed — nothing can roll it back."""

    __slots__ = ()


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
            raise RollbackFailedError(rollback_error) from rollback_error
        return RolledBack(value)

    async def _rollback_after_failure(self, primary_error: BaseException) -> Aborted:
        try:
            await self._run_rollback()
        except BaseException as rollback_error:
            fatal = RollbackFailedError(rollback_error, primary_error)
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


def reraise_transaction_fatal(error: BaseException) -> Never:
    """Unwrap a group-wrapped or bare transaction fatal to its underlying error, else re-raise the original.

    A bare fatal or a deferrable teardown-group fatal unwraps to ``raise fatal.error from
    fatal.primary_error``. A non-extractable error (cancellation, any other ``BaseException``) or a group
    still carrying a control-flow leaf re-raises the original so cancellation is never demoted to a
    recoverable failure.
    """
    fatal = extract_transaction_execution_error(error)
    if fatal is None or (fatal is not error and not can_defer_transaction_fatal(error, fatal)):
        raise error
    raise fatal.error from fatal.primary_error


def require_committed(result: Committed[_CommitT] | RolledBack[Never] | Aborted) -> _CommitT:
    if isinstance(result, Committed):
        return result.value
    if isinstance(result, Aborted):
        raise result.error
    if isinstance(result, RolledBack):
        assert_never(result.value)
    assert_never(result)


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
            raise AfterCommitError(error) from error
        if isinstance(result, Aborted):
            # The body returned Aborted(handler_error) after a clean rollback; a teardown failure must not
            # discard that handler-error evidence, so chain it as the propagating error's cause.
            raise error from result.error
        # A child-scope teardown failure masks the execution's fatal or cancellation into __context__,
        # where extract_transaction_execution_error / split cannot reach it. Re-surface both as group
        # leaves so the fatal stays extractable and any control-flow leaf stays present for the owner's law.
        if (
            execution_error is not None
            and error is not execution_error
            and extract_transaction_execution_error(error) is None
            and (
                extract_transaction_execution_error(execution_error) is not None
                or _has_control_flow_leaf(execution_error)
            )
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
            raise AfterCommitError(error) from error
    return result


async def run_committed(
    container: AsyncContainer,
    operation: Callable[[AsyncContainer], Awaitable[TransactionDecision[_CommitT, Never]]],
) -> _CommitT:
    """Run *operation* in a fresh child UoW scope and return its committed value, or raise the fatal/abort.

    Commit-or-fail sugar for scope-owning agents: ``require_committed(await execute_in_uow_scope(...))``.
    Owners that branch on ``Committed``/``Aborted``/``RolledBack`` keep the explicit two-step form.
    """
    return require_committed(await execute_in_uow_scope(container, operation))
