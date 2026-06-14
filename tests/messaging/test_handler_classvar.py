from __future__ import annotations

from typing import TYPE_CHECKING

from waku.messaging.behaviors.transactional import TransactionalBehavior
from waku.messaging.contracts.event import IEvent
from waku.messaging.errors.policy import ErrorPolicy
from waku.messaging.handler import EventHandler

if TYPE_CHECKING:
    from collections.abc import Sequence
    from typing import ClassVar


class _Evt(IEvent):
    pass


class TestErrorPoliciesClassVar:
    @staticmethod
    def test_default_is_empty() -> None:
        class H(EventHandler[_Evt]):
            async def handle(self, message: _Evt) -> None: ...

        assert H.error_policies == ()

    @staticmethod
    def test_declared_policies_readable_directly() -> None:
        policy = ErrorPolicy.on_any_exception().retry(max_attempts=2)

        class H(EventHandler[_Evt]):
            error_policies = (policy,)

            async def handle(self, message: _Evt) -> None: ...

        assert H.error_policies == (policy,)

    @staticmethod
    def test_child_inherits_parent_when_not_declared() -> None:
        policy = ErrorPolicy.on_any_exception().discard()

        class Base(EventHandler[_Evt]):
            error_policies = (policy,)

            async def handle(self, message: _Evt) -> None: ...

        class Child(Base):
            pass

        assert Child.error_policies == (policy,)

    @staticmethod
    def test_child_declaration_replaces_wholesale() -> None:
        parent_policy = ErrorPolicy.on_any_exception().discard()
        child_policy = ErrorPolicy.on_any_exception().retry(max_attempts=5)

        class Base(EventHandler[_Evt]):
            error_policies = (parent_policy,)

            async def handle(self, message: _Evt) -> None: ...

        class Child(Base):
            error_policies = (child_policy,)

        assert Child.error_policies == (child_policy,)

    @staticmethod
    def test_child_extends_via_explicit_unpacking() -> None:
        parent_policy = ErrorPolicy.on_any_exception().discard()
        extra_policy = ErrorPolicy.on_exception(TimeoutError).retry(max_attempts=3)

        class Base(EventHandler[_Evt]):
            error_policies: ClassVar[Sequence[ErrorPolicy]] = (parent_policy,)

            async def handle(self, message: _Evt) -> None: ...

        class Child(Base):
            error_policies = (*Base.error_policies, extra_policy)

        assert Child.error_policies == (parent_policy, extra_policy)


class TestAdditionalBehaviorsClassVar:
    @staticmethod
    def test_default_is_empty() -> None:
        class H(EventHandler[_Evt]):
            async def handle(self, message: _Evt) -> None: ...

        assert H.additional_behaviors == ()

    @staticmethod
    def test_declared_behaviors_readable_directly() -> None:
        class H(EventHandler[_Evt]):
            additional_behaviors = (TransactionalBehavior,)

            async def handle(self, message: _Evt) -> None: ...

        assert H.additional_behaviors == (TransactionalBehavior,)
