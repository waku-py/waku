import time
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from dishka.exceptions import NoFactoryError

from waku.messaging.behaviors.transactional import _TransactionDepth, run_in_transaction
from waku.messaging.endpoints.executor import ExecutionOutcome
from waku.messaging.exceptions import HandlerNotFound
from waku.messaging.observability.observer import INVOKE_DESTINATION, MessageObservers
from waku.messaging.pipeline.invoker import HandlerPipelineInvoker
from waku.messaging.registry import MessageRegistry
from waku.uow import IUnitOfWork

if TYPE_CHECKING:
    from waku.di import AsyncContainer
    from waku.messaging.contracts.envelope import MessageEnvelope
    from waku.messaging.contracts.event import IEvent
    from waku.messaging.contracts.handler import HandlerType
    from waku.messaging.contracts.message import ResponseT
    from waku.messaging.contracts.request import IRequest


class MessageDispatcher:
    __slots__ = ('_invoker', '_observers', '_registry')

    def __init__(
        self,
        registry: MessageRegistry,
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
            HandlerNotFound: If no handler is registered for the request type.
        """
        request_type = type(envelope.payload)
        handlers = self._registry.handler_map.get_handler_types(request_type)
        if len(handlers) == 0:
            raise HandlerNotFound(request_type)
        return await self._observed_invoke(scope, envelope, handlers[0])  # type: ignore[no-any-return]  # pyrefly: ignore[bad-return]

    async def invoke_event(self, scope: 'AsyncContainer', envelope: 'MessageEnvelope[IEvent]') -> None:
        """Resolve and execute ALL handlers for *envelope*'s event inline within the caller's *scope*.

        Handlers run sequentially, fail-fast: the first handler exception aborts the
        remaining handlers and propagates. All handlers are resolved up front, so an
        empty handler set raises ``HandlerNotFound`` before any handler side effect.
        Fires the ``executing``/``executed`` execution-lifecycle hooks per handler under
        ``INVOKE_DESTINATION``.

        Execution order is NOT a public contract — handlers for one event are independent.
        ``invoke`` is inline same-transaction execution: when a UoW is configured, the
        dispatcher owns ONE transaction frame around the whole fan-out so the N per-handler
        ``TransactionalBehavior`` frames join it (one commit over all N handlers + any nested
        invoke). Without a UoW it degrades to sequential fail-fast (same as ``invoke(request)``).

        Raises:
            HandlerNotFound: If no handler is registered for the event type.
        """
        event_type = type(envelope.payload)
        handlers = self._registry.handler_map.get_handler_types(event_type)
        if len(handlers) == 0:
            raise HandlerNotFound(event_type)

        async def _run_all() -> None:
            for handler_type in handlers:
                await self._observed_invoke(scope, envelope, handler_type)

        uow = await self._try_get_uow(scope)
        if uow is None:
            await _run_all()
            return
        depth = await scope.get(_TransactionDepth)
        await run_in_transaction(uow, depth, _run_all)

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
    async def _try_get_uow(scope: 'AsyncContainer') -> 'IUnitOfWork | None':
        try:
            return await scope.get(IUnitOfWork)
        except NoFactoryError:
            return None
