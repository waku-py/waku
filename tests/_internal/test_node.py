from __future__ import annotations

import os
import socket
from datetime import timedelta

import pytest

from waku import NodeIdentity, NodeRegistryConfig
from waku.exceptions import ImproperlyConfiguredError


class TestNodeIdentity:
    @staticmethod
    def test_two_nodes_sharing_a_label_get_distinct_identities() -> None:
        first = NodeIdentity.create('orders-worker')
        second = NodeIdentity.create('orders-worker')

        assert first.node_id != second.node_id

    @staticmethod
    def test_blank_description_defaults_to_hostname_and_pid() -> None:
        assert NodeIdentity.create().description == f'{socket.gethostname()}:{os.getpid()}'

    @staticmethod
    def test_configured_label_is_kept_as_description() -> None:
        assert NodeIdentity.create('orders-worker-3').description == 'orders-worker-3'


class TestNodeRegistryConfigValidation:
    @staticmethod
    @pytest.mark.parametrize('value', [timedelta(0), timedelta(microseconds=-1)])
    def test_heartbeat_interval_must_be_positive(value: timedelta) -> None:
        with pytest.raises(ImproperlyConfiguredError, match=r'NodeRegistryConfig\.heartbeat_interval must be positive'):
            NodeRegistryConfig(heartbeat_interval=value)

    @staticmethod
    @pytest.mark.parametrize('value', [timedelta(0), timedelta(microseconds=-1)])
    def test_stale_after_must_be_positive(value: timedelta) -> None:
        with pytest.raises(ImproperlyConfiguredError, match=r'NodeRegistryConfig\.stale_after must be positive'):
            NodeRegistryConfig(stale_after=value)

    @staticmethod
    @pytest.mark.parametrize('value', [timedelta(0), timedelta(microseconds=-1)])
    def test_evict_interval_must_be_positive(value: timedelta) -> None:
        with pytest.raises(ImproperlyConfiguredError, match=r'NodeRegistryConfig\.evict_interval must be positive'):
            NodeRegistryConfig(evict_interval=value)

    @staticmethod
    @pytest.mark.parametrize('value', [timedelta(0), timedelta(microseconds=-1)])
    def test_stop_timeout_must_be_positive(value: timedelta) -> None:
        with pytest.raises(ImproperlyConfiguredError, match=r'NodeRegistryConfig\.stop_timeout must be positive'):
            NodeRegistryConfig(stop_timeout=value)

    @staticmethod
    def test_stale_after_below_three_times_interval_fails_loud() -> None:
        with pytest.raises(ImproperlyConfiguredError, match=r'stale_after must be at least 3x heartbeat_interval'):
            NodeRegistryConfig(heartbeat_interval=timedelta(seconds=10), stale_after=timedelta(seconds=29))

    @staticmethod
    def test_stale_after_exactly_three_times_interval_is_accepted() -> None:
        # One second below this same pairing raises (see the sibling above); exactly at the floor must
        # construct, so not raising IS the observation.
        NodeRegistryConfig(heartbeat_interval=timedelta(seconds=10), stale_after=timedelta(seconds=30))

    @staticmethod
    def test_shipped_defaults_are_a_ten_second_heartbeat_and_sixty_second_staleness() -> None:
        config = NodeRegistryConfig()

        assert (config.heartbeat_interval, config.stale_after) == (timedelta(seconds=10), timedelta(seconds=60))
