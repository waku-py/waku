import logging
from dataclasses import dataclass
from typing import Annotated

import pytest

from waku.exceptions import ImproperlyConfiguredError
from waku.messaging.contracts.message import IMessage
from waku.messaging.observability.audit import Audit, AuditedMemberResolver

from tests.messaging.observability._unresolvable_message import Bad


@dataclass
class _Transfer(IMessage):
    account_id: Annotated[str, Audit()]
    amount: Annotated[int, Audit(heading='Amount')]
    note: str = ''


@dataclass
class _Vendor(IMessage):
    account_id: str = ''
    secret: str = ''


@dataclass
class _Plain(IMessage):
    x: int = 0


def test_extract_returns_annotated_members_with_heading_rename() -> None:
    resolver = AuditedMemberResolver(overrides={})
    assert resolver.extract(_Transfer(account_id='a1', amount=5, note='n')) == {'account_id': 'a1', 'Amount': 5}


def test_config_override_audits_unannotated_type() -> None:
    resolver = AuditedMemberResolver(overrides={_Vendor: ('account_id',)})
    assert resolver.extract(_Vendor(account_id='x', secret='s')) == {'account_id': 'x'}  # noqa: S106


def test_annotated_and_override_union_on_same_type() -> None:
    resolver = AuditedMemberResolver(overrides={_Transfer: ('note',)})
    assert resolver.extract(_Transfer(account_id='a1', amount=5, note='n')) == {
        'account_id': 'a1',
        'Amount': 5,
        'note': 'n',
    }


def test_no_audited_members_returns_empty() -> None:
    assert AuditedMemberResolver(overrides={}).extract(_Plain()) == {}


def test_resolve_returns_the_cached_tuple_on_repeat() -> None:
    resolver = AuditedMemberResolver(overrides={})
    assert resolver.resolve(_Transfer) is resolver.resolve(_Transfer)


def test_config_typo_on_resolvable_type_raises() -> None:
    resolver = AuditedMemberResolver(overrides={_Vendor: ('no_such_field',)})
    with pytest.raises(ImproperlyConfiguredError):
        resolver.resolve(_Vendor)


def test_missing_attr_at_extract_is_skipped_with_warning(caplog: pytest.LogCaptureFixture) -> None:
    resolver = AuditedMemberResolver(overrides={Bad: ('ghost',)})
    with caplog.at_level(logging.WARNING):
        assert resolver.extract(Bad()) == {}
    assert any('ghost' in r.message for r in caplog.records)


def test_unresolvable_hints_degrade_to_empty_with_warning(caplog: pytest.LogCaptureFixture) -> None:
    resolver = AuditedMemberResolver(overrides={})
    with caplog.at_level(logging.WARNING):
        assert resolver.resolve(Bad) == ()
    assert any('Cannot introspect' in r.message for r in caplog.records)


def test_resolve_memoization_suppresses_repeat_introspection_warnings(caplog: pytest.LogCaptureFixture) -> None:
    resolver = AuditedMemberResolver(overrides={})
    with caplog.at_level(logging.WARNING):
        resolver.resolve(Bad)
        resolver.resolve(Bad)
    assert len([r for r in caplog.records if 'Cannot introspect' in r.message]) == 1
