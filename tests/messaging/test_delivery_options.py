from datetime import UTC, datetime, timedelta, timezone

import pytest

from waku.messaging import DeliveryOptions, InvalidDeliveryOptionsError
from waku.messaging.exceptions import (
    DeliveryOptionNotApplicableError,
    SchedulingNotSupportedError,
)

_DT = datetime(2026, 6, 21, tzinfo=UTC)


def test_scheduled_time_and_delay_together_raise() -> None:
    with pytest.raises(InvalidDeliveryOptionsError):
        DeliveryOptions(scheduled_time=_DT, schedule_delay=timedelta(seconds=5))


def test_deliver_by_and_within_together_raise() -> None:
    with pytest.raises(InvalidDeliveryOptionsError):
        DeliveryOptions(deliver_by=_DT, deliver_within=timedelta(seconds=5))


def test_negative_schedule_delay_raises() -> None:
    with pytest.raises(InvalidDeliveryOptionsError):
        DeliveryOptions(schedule_delay=timedelta(seconds=-1))


def test_negative_deliver_within_raises() -> None:
    with pytest.raises(InvalidDeliveryOptionsError):
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


def test_invalid_delivery_options_error_preserves_reason_and_string() -> None:
    error = InvalidDeliveryOptionsError('choose one scheduling mode')

    assert error.reason == 'choose one scheduling mode'
    assert str(error) == 'Invalid delivery options: choose one scheduling mode'


def test_naive_scheduled_time_raises() -> None:
    value = datetime(2026, 6, 21)  # noqa: DTZ001 -- intentionally exercises naive input

    with pytest.raises(InvalidDeliveryOptionsError, match=r'scheduled_time.*timezone-aware'):
        DeliveryOptions(scheduled_time=value)


def test_naive_deliver_by_raises() -> None:
    value = datetime(2026, 6, 21)  # noqa: DTZ001 -- intentionally exercises naive input

    with pytest.raises(InvalidDeliveryOptionsError, match=r'deliver_by.*timezone-aware'):
        DeliveryOptions(deliver_by=value)


def test_aware_absolute_times_preserve_the_supplied_offset() -> None:
    value = datetime(2026, 6, 21, tzinfo=timezone(timedelta(hours=7)))

    options = DeliveryOptions(scheduled_time=value, deliver_by=value)

    assert options.scheduled_time is value
    assert options.deliver_by is value


def test_scheduling_fields_set_is_empty_when_unset() -> None:
    assert DeliveryOptions().scheduling_fields_set() == ()


def test_scheduling_fields_set_names_absolute_scheduled_time() -> None:
    assert DeliveryOptions(scheduled_time=_DT).scheduling_fields_set() == ('scheduled_time',)


def test_scheduling_fields_set_names_relative_schedule_delay() -> None:
    assert DeliveryOptions(schedule_delay=timedelta(seconds=5)).scheduling_fields_set() == ('schedule_delay',)


def test_expiry_fields_set_is_empty_when_unset() -> None:
    assert DeliveryOptions().expiry_fields_set() == ()


def test_expiry_fields_set_names_absolute_deliver_by() -> None:
    assert DeliveryOptions(deliver_by=_DT).expiry_fields_set() == ('deliver_by',)


def test_expiry_fields_set_names_relative_deliver_within() -> None:
    assert DeliveryOptions(deliver_within=timedelta(seconds=5)).expiry_fields_set() == ('deliver_within',)


def test_resolve_scheduled_time_returns_absolute_when_set() -> None:
    now = datetime(2026, 6, 21, 12, 0, tzinfo=UTC)
    assert DeliveryOptions(scheduled_time=_DT).resolve_scheduled_time(now) == _DT


def test_resolve_scheduled_time_folds_delay_onto_now() -> None:
    now = datetime(2026, 6, 21, 12, 0, tzinfo=UTC)
    assert DeliveryOptions(schedule_delay=timedelta(seconds=30)).resolve_scheduled_time(now) == now + timedelta(
        seconds=30,
    )


def test_resolve_scheduled_time_is_none_when_unset() -> None:
    now = datetime(2026, 6, 21, 12, 0, tzinfo=UTC)
    assert DeliveryOptions().resolve_scheduled_time(now) is None


def test_resolve_expiry_returns_absolute_when_set() -> None:
    now = datetime(2026, 6, 21, 12, 0, tzinfo=UTC)
    assert DeliveryOptions(deliver_by=_DT).resolve_expiry(now) == _DT


def test_resolve_expiry_folds_within_onto_now() -> None:
    now = datetime(2026, 6, 21, 12, 0, tzinfo=UTC)
    assert DeliveryOptions(deliver_within=timedelta(seconds=45)).resolve_expiry(now) == now + timedelta(seconds=45)


def test_resolve_expiry_is_none_when_unset() -> None:
    now = datetime(2026, 6, 21, 12, 0, tzinfo=UTC)
    assert DeliveryOptions().resolve_expiry(now) is None
