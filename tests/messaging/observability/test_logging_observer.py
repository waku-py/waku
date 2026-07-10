import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, cast
from uuid import uuid4

import pytest

from waku.messaging.contracts.envelope import MessageEnvelope
from waku.messaging.contracts.handler import HandlerType
from waku.messaging.contracts.message import IMessage
from waku.messaging.endpoints.executor import ExecutionOutcome
from waku.messaging.observability.audit import Audit, AuditedMemberResolver
from waku.messaging.observability.logging_observer import LoggingMessageObserver


@dataclass
class _Transfer(IMessage):
    account_id: Annotated[str, Audit()] = ''
    amount: Annotated[int, Audit(heading='Amount')] = 0


def _envelope(payload: IMessage) -> MessageEnvelope[Any]:
    cid = str(uuid4())
    return MessageEnvelope(
        message_id=uuid4(),
        correlation_id=cid,
        causation_id=cid,
        message_type='Transfer',
        timestamp=datetime.now(tz=UTC),
        payload=payload,
        group_id='acct-1',
    )


pytestmark = pytest.mark.anyio


async def test_executed_success_logs_info_with_fields_and_audit(caplog: pytest.LogCaptureFixture) -> None:
    obs = LoggingMessageObserver(AuditedMemberResolver(overrides={}))
    with caplog.at_level(logging.DEBUG, logger='waku.message.Transfer'):
        await obs.on_executed(
            _envelope(_Transfer('a1', 5)),
            'queue-a',
            cast('HandlerType', object),
            ExecutionOutcome.SUCCESS,
            None,
            timedelta(milliseconds=12),
        )
    rec = next(r for r in caplog.records if r.name == 'waku.message.Transfer')
    assert rec.levelno == logging.INFO
    assert rec.outcome == 'SUCCESS'  # type: ignore[attr-defined]
    assert rec.duration_ms == pytest.approx(12.0)  # type: ignore[attr-defined]
    assert rec.group_id == 'acct-1'  # type: ignore[attr-defined]
    assert rec.audit == {'account_id': 'a1', 'Amount': 5}  # type: ignore[attr-defined]
    assert rec.destination == 'queue-a'  # type: ignore[attr-defined]


async def test_executed_dead_letter_logs_error_summary_no_traceback(caplog: pytest.LogCaptureFixture) -> None:
    obs = LoggingMessageObserver(AuditedMemberResolver(overrides={}))
    with caplog.at_level(logging.DEBUG, logger='waku.message.Transfer'):
        await obs.on_executed(
            _envelope(_Transfer()),
            'queue-a',
            cast('HandlerType', object),
            ExecutionOutcome.DEAD_LETTERED,
            ValueError('nope'),
            timedelta(),
        )
    rec = next(r for r in caplog.records if r.name == 'waku.message.Transfer')
    assert rec.levelno == logging.ERROR
    assert rec.error_type == 'ValueError'  # type: ignore[attr-defined]
    assert rec.error_message == 'nope'  # type: ignore[attr-defined]
    assert rec.exc_info is None


@pytest.mark.parametrize(
    ('outcome', 'level'),
    [
        (ExecutionOutcome.SUCCESS, logging.INFO),
        (ExecutionOutcome.DISCARDED, logging.INFO),
        (ExecutionOutcome.REQUEUED, logging.WARNING),
        (ExecutionOutcome.PAUSED, logging.WARNING),
        (ExecutionOutcome.DEAD_LETTERED, logging.ERROR),
        (ExecutionOutcome.DEAD_LETTER_FAILED, logging.ERROR),
        (ExecutionOutcome.FAILED_NO_POLICY, logging.ERROR),
    ],
)
async def test_executed_level_by_outcome(
    outcome: ExecutionOutcome, level: int, caplog: pytest.LogCaptureFixture
) -> None:
    obs = LoggingMessageObserver(AuditedMemberResolver(overrides={}))
    with caplog.at_level(logging.DEBUG, logger='waku.message.Transfer'):
        await obs.on_executed(
            _envelope(_Transfer()), 'queue-a', cast('HandlerType', object), outcome, None, timedelta()
        )
    assert next(r for r in caplog.records if r.name == 'waku.message.Transfer').levelno == level


async def test_sent_and_executing_log_debug(caplog: pytest.LogCaptureFixture) -> None:
    obs = LoggingMessageObserver(AuditedMemberResolver(overrides={}))
    env = _envelope(_Transfer())
    with caplog.at_level(logging.DEBUG, logger='waku.message.Transfer'):
        await obs.on_sent(env, 'queue-a')
        await obs.on_executing(env, 'queue-a', cast('HandlerType', object))
    records = [r for r in caplog.records if r.name == 'waku.message.Transfer']
    assert [r.levelno for r in records] == [logging.DEBUG, logging.DEBUG]
    executing_rec = next(r for r in records if r.message == 'executing')
    assert executing_rec.destination == 'queue-a'  # type: ignore[attr-defined]


async def test_audit_field_named_like_reserved_key_is_namespaced(caplog: pytest.LogCaptureFixture) -> None:
    # an audited member named 'outcome' lands under audit['outcome'], never shadowing the top-level field
    @dataclass
    class _Collide(IMessage):
        outcome: Annotated[str, Audit()] = 'biz'

    env = MessageEnvelope(
        message_id=uuid4(),
        correlation_id=str(uuid4()),
        causation_id=str(uuid4()),
        message_type='Collide',
        timestamp=datetime.now(tz=UTC),
        payload=_Collide(),
    )
    obs = LoggingMessageObserver(AuditedMemberResolver(overrides={}))
    with caplog.at_level(logging.DEBUG, logger='waku.message.Collide'):
        await obs.on_executed(env, 'queue-a', cast('HandlerType', object), ExecutionOutcome.SUCCESS, None, timedelta())
    rec = next(r for r in caplog.records if r.name == 'waku.message.Collide')
    assert rec.outcome == 'SUCCESS'  # type: ignore[attr-defined]  # reserved field intact
    assert rec.audit == {'outcome': 'biz'}  # type: ignore[attr-defined]  # business field namespaced


async def test_disabled_level_skips_emission(caplog: pytest.LogCaptureFixture) -> None:
    obs = LoggingMessageObserver(AuditedMemberResolver(overrides={}))
    logging.getLogger('waku.message.Transfer').setLevel(logging.CRITICAL)
    try:
        with caplog.at_level(logging.DEBUG):
            await obs.on_sent(_envelope(_Transfer()), 'queue-a')
        assert not [r for r in caplog.records if r.name == 'waku.message.Transfer']
    finally:
        logging.getLogger('waku.message.Transfer').setLevel(logging.NOTSET)
