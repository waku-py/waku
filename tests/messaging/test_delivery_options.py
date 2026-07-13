from datetime import UTC, datetime, timedelta

import pytest

from waku.messaging.delivery import DeliveryOptions
from waku.messaging.exceptions import (
    ConflictingDeliveryOptionsError,
    DeliveryOptionNotApplicableError,
    SchedulingNotSupportedError,
)

_DT = datetime(2026, 6, 21, tzinfo=UTC)


def test_scheduled_time_and_delay_together_raise() -> None:
    with pytest.raises(ConflictingDeliveryOptionsError):
        DeliveryOptions(scheduled_time=_DT, schedule_delay=timedelta(seconds=5))


def test_deliver_by_and_within_together_raise() -> None:
    with pytest.raises(ConflictingDeliveryOptionsError):
        DeliveryOptions(deliver_by=_DT, deliver_within=timedelta(seconds=5))


def test_negative_schedule_delay_raises() -> None:
    with pytest.raises(ConflictingDeliveryOptionsError):
        DeliveryOptions(schedule_delay=timedelta(seconds=-1))


def test_negative_deliver_within_raises() -> None:
    with pytest.raises(ConflictingDeliveryOptionsError):
        DeliveryOptions(deliver_within=timedelta(seconds=-1))


def test_single_sided_scheduling_options_construct() -> None:
    assert DeliveryOptions(scheduled_time=_DT).scheduled_time == _DT
    assert DeliveryOptions(schedule_delay=timedelta(seconds=5)).schedule_delay == timedelta(seconds=5)


def test_single_sided_expiration_options_construct() -> None:
    assert DeliveryOptions(deliver_by=_DT).deliver_by == _DT
    assert DeliveryOptions(deliver_within=timedelta(seconds=5)).deliver_within == timedelta(seconds=5)


def test_tenant_id_defaults_none() -> None:
    assert DeliveryOptions().tenant_id is None


def test_tenant_id_is_carried() -> None:
    assert DeliveryOptions(tenant_id='t1').tenant_id == 't1'


def test_not_applicable_error_names_option_and_verb() -> None:
    err = DeliveryOptionNotApplicableError('scheduled_time', 'invoke')
    assert 'scheduled_time' in str(err)
    assert 'invoke' in str(err)


def test_scheduling_not_supported_names_uri() -> None:
    assert 'orders' in str(SchedulingNotSupportedError('orders'))
