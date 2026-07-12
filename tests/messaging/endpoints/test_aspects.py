from __future__ import annotations

from waku._internal.sentinel import MISSING
from waku.messaging.endpoints._internal.aspects import ListenAspect, SendAspect


class TestListenAspectDefaults:
    @staticmethod
    def test_max_requeue_attempts_defaults_to_inherit() -> None:
        aspect = ListenAspect()
        assert aspect.max_requeue_attempts is MISSING  # type: ignore[comparison-overlap]  # mypy lacks PEP 661 sentinel support

    @staticmethod
    def test_circuit_breaker_defaults_to_inherit() -> None:
        aspect = ListenAspect()
        assert aspect.circuit_breaker is MISSING  # type: ignore[comparison-overlap]  # mypy lacks PEP 661 sentinel support

    @staticmethod
    def test_backpressure_defaults_to_none() -> None:
        aspect = ListenAspect()
        assert aspect.backpressure is None


class TestSendAspectDefaults:
    @staticmethod
    def test_sending_failure_policies_defaults_to_empty() -> None:
        aspect = SendAspect()
        assert aspect.sending_failure_policies == ()
