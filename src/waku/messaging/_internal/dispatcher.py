import time
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any, cast

from waku._internal.transaction import (
    AfterCommitError,
    extract_transaction_execution_error,
)
from waku.messaging._internal.outbox_cascading import DeferredCascadeFlusher
from waku.messaging._internal.transaction import TransactionDepth
from waku.messaging.behaviors.transactional import run_in_transaction
from waku.messaging.endpoints.outcome import ExecutionOutcome
from waku.messaging.exceptions import HandlerNotFoundError
from waku.messaging.handler_map import HandlerMap
from waku.messaging.observability.observer import INVOKE_DESTINATION, MessageObservers
from waku.messaging.outgoing import IOutgoingMessagesFrames
from waku.messaging.pipeline._internal.invoker import HandlerPipelineInvoker
from waku.uow import IUnitOfWork

if TYPE_CHECKING:
    from waku.di import AsyncContainer
    from waku.messages import IEvent
    from waku.messaging.contracts.envelope import MessageEnvelope
    from waku.messaging.contracts.handler import HandlerType
    from waku.messaging.contracts.message import ResponseT
    from waku.messaging.contracts.request import IRequest
    from waku.messaging.outgoing import DeferredCascadeBatch


@dataclass(frozen=True, slots=True)
class _HandlerObservation:
    handler_type: 'HandlerType'
    duration: timedelta
    error: Exception | None


class MessageDispatcher:
    __slots__ = ('_invoker', '_observers', '_registry')

    def __init__(
        self,
        handler_map: HandlerMap,
        invoker: HandlerPipelineInvoker,
        observers: MessageObservers,
    ) -> None:
        self._registry = handler_map
        self._invoker = invoker
        self._observers = observers

    async def invoke_request(
        self, scope: 'AsyncContainer', envelope: 'MessageEnvelope[IRequest[ResponseT]]'
    ) -> 'ResponseT':
        """Resolve and execute the handler for *envelope*'s request within the caller's *scope*.

        The handler shares the caller's DI scope (and its transactional context).
        This is intentional: ``invoke`` is inline request/response, not fire-and-forget.
        Fires the ``executing``/``executed`` execution-lifecycle hooks under ``INVOKE_DESTINATION``.

        Raises:
            HandlerNotFoundError: If no handler is registered for the request type.
        """
        request_type = type(envelope.payload)
        handlers = self._registry.get_handler_types(request_type)
        if len(handlers) == 0:
            raise HandlerNotFoundError(request_type)
        handler_type = handlers[0]
        if self._invoker.has_transaction(handler_type):
            result = await self._run_transactional(scope, envelope, handler_type)
        else:
            result = await self._run_direct(scope, envelope, handler_type)
        return cast('ResponseT', result)

    async def invoke_event(self, scope: 'AsyncContainer', envelope: 'MessageEnvelope[IEvent]') -> None:
        """Resolve and execute ALL handlers for *envelope*'s event inline within the caller's *scope*.

        Handlers run sequentially, fail-fast: the first handler exception aborts the
        remaining handlers and propagates. All handlers are resolved up front, so an
        empty handler set raises ``HandlerNotFoundError`` before any handler side effect.
        Fires the ``executing``/``executed`` execution-lifecycle hooks per handler under
        ``INVOKE_DESTINATION``.

        Execution order is NOT a public contract — handlers for one event are independent.
        A transactional fan-out owns one transaction frame so every per-handler
        ``TransactionalBehavior`` joins one physical transaction. A direct fan-out resolves no UoW.

        Raises:
            HandlerNotFoundError: If no handler is registered for the event type.
        """
        event_type = type(envelope.payload)
        handlers = self._registry.get_handler_types(event_type)
        if len(handlers) == 0:
            raise HandlerNotFoundError(event_type)

        observations: list[_HandlerObservation] = []

        async def _run_all() -> None:
            for handler_type in handlers:
                await self._invoke_provisionally(scope, envelope, handler_type, observations)

        if any(self._invoker.has_transaction(handler_type) for handler_type in handlers):
            depth = await scope.get(TransactionDepth)
            uow = await scope.get(IUnitOfWork)
            try:
                await run_in_transaction(uow, depth, _run_all)
            except BaseException as error:
                await self._discard_cascades_if_owner(scope)
                if extract_transaction_execution_error(error) is None and isinstance(error, Exception):
                    await self._emit_failed(envelope, observations, error)
                raise
            await self._flush_transactional_cascades_if_owner(scope)
            await self._emit_observations(envelope, observations)
            return

        try:
            await _run_all()
        except BaseException:
            await self._discard_cascades_if_owner(scope)
            await self._emit_observations(envelope, observations)
            raise
        await self._flush_direct_cascades_if_owner(scope)
        await self._emit_observations(envelope, observations)

    async def dispatch_to_handler(
        self, scope: 'AsyncContainer', envelope: 'MessageEnvelope[Any]', handler_type: 'HandlerType'
    ) -> Any:
        """Resolve and execute exactly *handler_type* for *envelope* within the caller's *scope*.

        The replay/reprocess primitive: ONE specific handler, full pipeline, NO error policies —
        a handler failure propagates to the caller (which records the outcome) instead of
        re-escalating through policy evaluation. Fires the ``executing``/``executed``
        execution-lifecycle hooks under ``INVOKE_DESTINATION``.
        """
        if self._invoker.has_transaction(handler_type):
            return await self._run_transactional(scope, envelope, handler_type)
        return await self._run_direct(scope, envelope, handler_type)

    async def _run_transactional(
        self,
        scope: 'AsyncContainer',
        envelope: 'MessageEnvelope[Any]',
        handler_type: 'HandlerType',
    ) -> Any:
        observations: list[_HandlerObservation] = []
        try:
            result = await self._invoke_transactional(scope, envelope, handler_type, observations)
        except BaseException as error:
            await self._discard_cascades_if_owner(scope)
            if extract_transaction_execution_error(error) is None and isinstance(error, Exception):
                await self._emit_failed(envelope, observations, error)
            raise
        await self._flush_transactional_cascades_if_owner(scope)
        await self._emit_observations(envelope, observations)
        return result

    async def _run_direct(
        self,
        scope: 'AsyncContainer',
        envelope: 'MessageEnvelope[Any]',
        handler_type: 'HandlerType',
    ) -> Any:
        observations: list[_HandlerObservation] = []
        try:
            result = await self._invoke_provisionally(scope, envelope, handler_type, observations)
        except BaseException:
            await self._discard_cascades_if_owner(scope)
            await self._emit_observations(envelope, observations)
            raise
        await self._flush_direct_cascades_if_owner(scope)
        await self._emit_observations(envelope, observations)
        return result

    async def _invoke_transactional(
        self,
        scope: 'AsyncContainer',
        envelope: 'MessageEnvelope[Any]',
        handler_type: 'HandlerType',
        observations: list[_HandlerObservation],
    ) -> Any:
        return await self._invoke_provisionally(
            scope,
            envelope,
            handler_type,
            observations,
            transactional=True,
        )

    async def _invoke_provisionally(
        self,
        scope: 'AsyncContainer',
        envelope: 'MessageEnvelope[Any]',
        handler_type: 'HandlerType',
        observations: list[_HandlerObservation],
        *,
        transactional: bool = False,
    ) -> Any:
        await self._observers.executing(envelope, INVOKE_DESTINATION, handler_type)
        start = time.perf_counter()
        try:
            if transactional:
                result = await self._invoker.invoke_transactional(scope, envelope.payload, handler_type)
            else:
                result = await self._invoker.invoke(scope, envelope.payload, handler_type)
        except Exception as error:
            duration = timedelta(seconds=time.perf_counter() - start)
            observations.append(_HandlerObservation(handler_type, duration, error))
            raise
        duration = timedelta(seconds=time.perf_counter() - start)
        observations.append(_HandlerObservation(handler_type, duration, None))
        return result

    async def _emit_observations(
        self,
        envelope: 'MessageEnvelope[Any]',
        observations: list[_HandlerObservation],
    ) -> None:
        for observation in observations:
            outcome = ExecutionOutcome.SUCCESS if observation.error is None else ExecutionOutcome.FAILED_NO_POLICY
            await self._observers.executed(
                envelope,
                INVOKE_DESTINATION,
                observation.handler_type,
                outcome,
                observation.error,
                observation.duration,
            )

    async def _emit_failed(
        self,
        envelope: 'MessageEnvelope[Any]',
        observations: list[_HandlerObservation],
        error: Exception,
    ) -> None:
        for observation in observations:
            await self._observers.executed(
                envelope,
                INVOKE_DESTINATION,
                observation.handler_type,
                ExecutionOutcome.FAILED_NO_POLICY,
                error,
                observation.duration,
            )

    @staticmethod
    async def _discard_cascades_if_owner(scope: 'AsyncContainer') -> None:
        depth = await scope.get(TransactionDepth)
        if depth.depth == 0:
            outgoing = await scope.get(IOutgoingMessagesFrames)
            outgoing.detach_deferred()

    async def _flush_direct_cascades_if_owner(self, scope: 'AsyncContainer') -> None:
        batch = await self._detach_cascades_if_owner(scope)
        if batch:
            await self._flush_in_fresh_scope(scope, batch)

    async def _flush_transactional_cascades_if_owner(self, scope: 'AsyncContainer') -> None:
        batch = await self._detach_cascades_if_owner(scope)
        if not batch:
            return
        try:
            await self._flush_in_fresh_scope(scope, batch)
        except BaseException as error:
            raise AfterCommitError(error) from error

    @staticmethod
    async def _detach_cascades_if_owner(scope: 'AsyncContainer') -> 'DeferredCascadeBatch':
        depth = await scope.get(TransactionDepth)
        if depth.depth != 0:
            return ()
        outgoing: IOutgoingMessagesFrames = await scope.get(IOutgoingMessagesFrames)
        return outgoing.detach_deferred()

    @staticmethod
    async def _flush_in_fresh_scope(scope: 'AsyncContainer', batch: 'DeferredCascadeBatch') -> None:
        parent = scope.parent_container
        container = parent if parent is not None else scope
        async with container() as child:
            flusher = await child.get(DeferredCascadeFlusher)
            await flusher.flush(batch)
