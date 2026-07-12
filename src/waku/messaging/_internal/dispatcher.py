import time
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from dishka.exceptions import NoFactoryError

from waku.messaging._internal.outbox_cascading import DeferredCascadeFlusher
from waku.messaging._internal.transaction import TransactionDepth
from waku.messaging._internal.uow import NoOpUnitOfWork
from waku.messaging.behaviors.transactional import run_in_transaction
from waku.messaging.endpoints.outcome import ExecutionOutcome
from waku.messaging.exceptions import HandlerNotFoundError
from waku.messaging.handler_map import HandlerMap
from waku.messaging.observability.observer import INVOKE_DESTINATION, MessageObservers
from waku.messaging.pipeline._internal.invoker import HandlerPipelineInvoker
from waku.uow import IUnitOfWork

if TYPE_CHECKING:
    from waku.di import AsyncContainer
    from waku.messages import IEvent
    from waku.messaging.contracts.envelope import MessageEnvelope
    from waku.messaging.contracts.handler import HandlerType
    from waku.messaging.contracts.message import ResponseT
    from waku.messaging.contracts.request import IRequest


class MessageDispatcher:
    __slots__ = ('_invoker', '_observers', '_registry')

    def __init__(
        self,
        registry: HandlerMap,
        invoker: HandlerPipelineInvoker,
        observers: MessageObservers,
    ) -> None:
        self._registry = registry
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
        return await self._observed_invoke(scope, envelope, handlers[0])  # type: ignore[no-any-return]  # pyrefly: ignore[bad-return]

    async def invoke_event(self, scope: 'AsyncContainer', envelope: 'MessageEnvelope[IEvent]') -> None:
        """Resolve and execute ALL handlers for *envelope*'s event inline within the caller's *scope*.

        Handlers run sequentially, fail-fast: the first handler exception aborts the
        remaining handlers and propagates. All handlers are resolved up front, so an
        empty handler set raises ``HandlerNotFoundError`` before any handler side effect.
        Fires the ``executing``/``executed`` execution-lifecycle hooks per handler under
        ``INVOKE_DESTINATION``.

        Execution order is NOT a public contract — handlers for one event are independent.
        ``invoke`` is inline same-transaction execution: the dispatcher owns ONE transaction
        frame around the whole fan-out so the N per-handler ``TransactionalBehavior`` frames
        join it (one commit over all N handlers + any nested invoke). When no UoW is registered
        the owning frame runs against a no-op unit of work, so the fan-out stays sequential
        fail-fast with no commit.

        Raises:
            HandlerNotFoundError: If no handler is registered for the event type.
        """
        event_type = type(envelope.payload)
        handlers = self._registry.get_handler_types(event_type)
        if len(handlers) == 0:
            raise HandlerNotFoundError(event_type)

        async def _run_all() -> None:
            for handler_type in handlers:
                await self._observed_invoke(scope, envelope, handler_type)

        uow = await self._resolve_uow(scope)
        depth = await scope.get(TransactionDepth)
        await run_in_transaction(uow, depth, _run_all)
        # The fan-out frame keeps depth >= 1 for every per-handler pipeline, so the per-handler
        # DeferredCascadingBehavior never flushes under invoke(event) — the dispatcher is the flush
        # owner once ITS frame has committed. depth stays > 0 when this invoke_event is itself
        # nested inside an open transaction: the true outermost owner flushes then.
        if depth.depth == 0:
            flusher = await scope.get(DeferredCascadeFlusher)
            await flusher.flush()

    async def dispatch_to_handler(
        self, scope: 'AsyncContainer', envelope: 'MessageEnvelope[Any]', handler_type: 'HandlerType'
    ) -> Any:
        """Resolve and execute exactly *handler_type* for *envelope* within the caller's *scope*.

        The replay/reprocess primitive: ONE specific handler, full pipeline, NO error policies —
        a handler failure propagates to the caller (which records the outcome) instead of
        re-escalating through policy evaluation. Fires the ``executing``/``executed``
        execution-lifecycle hooks under ``INVOKE_DESTINATION``.
        """
        return await self._observed_invoke(scope, envelope, handler_type)

    async def _observed_invoke(
        self, scope: 'AsyncContainer', envelope: 'MessageEnvelope[Any]', handler_type: 'HandlerType'
    ) -> Any:
        """Run one handler with execution-lifecycle observability (the invoke-path analog of the executor).

        Fires ``executing``/``executed`` per handler; a handler exception fires ``executed`` with
        ``FAILED_NO_POLICY`` (invoke consults no error policies — the failure propagates) and RE-RAISES.
        """
        await self._observers.executing(envelope, INVOKE_DESTINATION, handler_type)
        start = time.perf_counter()
        try:
            result = await self._invoker.invoke(scope, envelope.payload, handler_type)
        except Exception as exc:
            duration = timedelta(seconds=time.perf_counter() - start)
            await self._observers.executed(
                envelope, INVOKE_DESTINATION, handler_type, ExecutionOutcome.FAILED_NO_POLICY, exc, duration
            )
            raise
        duration = timedelta(seconds=time.perf_counter() - start)
        await self._observers.executed(
            envelope, INVOKE_DESTINATION, handler_type, ExecutionOutcome.SUCCESS, None, duration
        )
        return result

    @staticmethod
    async def _resolve_uow(scope: 'AsyncContainer') -> 'IUnitOfWork':
        # Null-provisioning seam (not the doctrine's target): a real UoW when registered, else the null
        # UoW. The noop is NOT put on the IUnitOfWork DI key — that would defeat the UoW presence checks.
        try:
            uow: IUnitOfWork = await scope.get(IUnitOfWork)
        except NoFactoryError:
            return NoOpUnitOfWork()
        return uow
