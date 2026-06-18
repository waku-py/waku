from __future__ import annotations

from waku.exceptions import ImproperlyConfiguredError
from waku.serialization.exceptions import UpcasterChainError


def test_upcaster_chain_error_is_config_error() -> None:
    error = UpcasterChainError('duplicate from_version')
    assert isinstance(error, ImproperlyConfiguredError)
    assert str(error) == 'duplicate from_version'
