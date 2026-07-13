from __future__ import annotations

from dataclasses import dataclass, make_dataclass
from typing import ClassVar

import pytest

from waku.exceptions import ImproperlyConfiguredError
from waku.messages import IMessage, MessageIdentity
from waku.messaging._internal.identity import MessageTypeRegistry, resolve_message_identity


@dataclass(frozen=True, slots=True)
class SampleEvent(IMessage):
    pass


@dataclass(frozen=True, slots=True)
class OtherEvent(IMessage):
    pass


@dataclass(frozen=True, slots=True)
class AnnotatedEvent(IMessage):
    message_identity: ClassVar[str | MessageIdentity] = 'annotated-event'


@dataclass(frozen=True, slots=True)
class VersionedEvent(IMessage):
    message_identity: ClassVar[str | MessageIdentity] = MessageIdentity(name='versioned', version=2)


@dataclass(frozen=True, slots=True)
class ChildOfAnnotated(AnnotatedEvent):
    pass


@dataclass(frozen=True, slots=True)
class EmptyIdentityEvent(IMessage):
    message_identity: ClassVar[str | MessageIdentity] = ''


class TestMessageIdentity:
    @staticmethod
    def test_str_for_version_1_omits_version_suffix() -> None:
        identity = MessageIdentity(name='order-placed', version=1)
        assert str(identity) == 'order-placed'

    @staticmethod
    def test_str_for_version_above_1_appends_v_suffix() -> None:
        identity = MessageIdentity(name='order-placed', version=2)
        assert str(identity) == 'order-placed.v2'

    @staticmethod
    def test_empty_name_rejected() -> None:
        with pytest.raises(ImproperlyConfiguredError, match='name must be non-empty'):
            MessageIdentity(name='')

    @staticmethod
    def test_version_below_1_rejected() -> None:
        with pytest.raises(ImproperlyConfiguredError, match='version must be >= 1'):
            MessageIdentity(name='x', version=0)


class TestResolveMessageIdentity:
    @staticmethod
    def test_classvar_string_wins_over_config_and_fqn() -> None:
        identity = resolve_message_identity(AnnotatedEvent, {AnnotatedEvent: 'from-config'})
        assert identity == MessageIdentity(name='annotated-event')

    @staticmethod
    def test_classvar_message_identity_carries_name_and_version() -> None:
        assert resolve_message_identity(VersionedEvent, {}) == MessageIdentity(name='versioned', version=2)

    @staticmethod
    def test_classvar_is_own_class_only_not_inherited() -> None:
        expected = f'{ChildOfAnnotated.__module__}.{ChildOfAnnotated.__qualname__}'
        assert resolve_message_identity(ChildOfAnnotated, {}) == MessageIdentity(name=expected)

    @staticmethod
    def test_config_override_used_when_no_classvar() -> None:
        assert resolve_message_identity(SampleEvent, {SampleEvent: 'sample-event'}) == MessageIdentity(
            name='sample-event'
        )

    @staticmethod
    def test_config_override_message_identity_carries_version() -> None:
        identity = resolve_message_identity(SampleEvent, {SampleEvent: MessageIdentity(name='sample', version=3)})
        assert identity == MessageIdentity(name='sample', version=3)

    @staticmethod
    def test_fqn_fallback_when_neither_classvar_nor_config() -> None:
        expected = f'{SampleEvent.__module__}.{SampleEvent.__qualname__}'
        assert resolve_message_identity(SampleEvent, {}) == MessageIdentity(name=expected)

    @staticmethod
    def test_empty_classvar_string_rejected() -> None:
        with pytest.raises(ImproperlyConfiguredError, match='empty string'):
            resolve_message_identity(EmptyIdentityEvent, {})

    @staticmethod
    def test_empty_config_override_rejected() -> None:
        with pytest.raises(ImproperlyConfiguredError, match='empty string'):
            resolve_message_identity(SampleEvent, {SampleEvent: ''})

    @staticmethod
    def test_message_identity_without_classvar_annotation_is_rejected() -> None:
        # dynamic: bypasses the now-static ClassVar check; exercises the runtime guard
        bad = make_dataclass(
            'DynamicMissingClassVar',
            [('message_identity', str)],
            bases=(IMessage,),
            frozen=True,
            slots=True,
        )
        with pytest.raises(ImproperlyConfiguredError, match='must be ClassVar'):
            resolve_message_identity(bad, {})


class TestMessageTypeRegistryResolveName:
    @staticmethod
    def test_classvar_identity_wins() -> None:
        registry = MessageTypeRegistry(identities={}, known_types=[AnnotatedEvent])
        assert registry.resolve_name(AnnotatedEvent) == 'annotated-event'

    @staticmethod
    def test_config_override_used_for_third_party_type() -> None:
        registry = MessageTypeRegistry(identities={SampleEvent: 'sample-event'}, known_types=[SampleEvent])
        assert registry.resolve_name(SampleEvent) == 'sample-event'

    @staticmethod
    def test_unmapped_type_falls_back_to_fqn() -> None:
        registry = MessageTypeRegistry(identities={}, known_types=[SampleEvent])
        expected = f'{SampleEvent.__module__}.{SampleEvent.__qualname__}'
        assert registry.resolve_name(SampleEvent) == expected

    @staticmethod
    def test_resolve_name_honors_classvar_even_for_unregistered_type() -> None:
        registry = MessageTypeRegistry(identities={}, known_types=[])
        assert registry.resolve_name(AnnotatedEvent) == 'annotated-event'

    @staticmethod
    def test_resolve_name_unregistered_type_honors_config_override() -> None:
        registry = MessageTypeRegistry(identities={SampleEvent: 'sample'}, known_types=[])
        assert registry.resolve_name(SampleEvent) == 'sample'

    @staticmethod
    def test_resolve_name_unregistered_type_falls_back_to_fqn() -> None:
        registry = MessageTypeRegistry(identities={}, known_types=[])
        expected = f'{SampleEvent.__module__}.{SampleEvent.__qualname__}'
        assert registry.resolve_name(SampleEvent) == expected

    @staticmethod
    def test_resolve_version_reflects_classvar_versioned_identity() -> None:
        registry = MessageTypeRegistry(identities={}, known_types=[VersionedEvent])
        assert registry.resolve_name(VersionedEvent) == 'versioned'
        assert registry.resolve_version(VersionedEvent) == 2


class TestMessageTypeRegistryResolveType:
    @staticmethod
    def test_name_to_type_for_aliased_type() -> None:
        registry = MessageTypeRegistry(
            identities={SampleEvent: 'sample-event'},
            known_types=[SampleEvent],
        )
        assert registry.resolve_type('sample-event') is SampleEvent

    @staticmethod
    def test_name_to_type_for_fqn_fallback() -> None:
        registry = MessageTypeRegistry(identities={}, known_types=[SampleEvent])
        fqn = f'{SampleEvent.__module__}.{SampleEvent.__qualname__}'
        assert registry.resolve_type(fqn) is SampleEvent

    @staticmethod
    def test_duplicate_name_rejected_at_construction() -> None:
        with pytest.raises(ImproperlyConfiguredError, match='duplicate message identity'):
            MessageTypeRegistry(
                identities={SampleEvent: 'x', OtherEvent: 'x'},
                known_types=[SampleEvent, OtherEvent],
            )

    @staticmethod
    def test_unknown_name_raises_with_registered_types_listed() -> None:
        registry = MessageTypeRegistry(
            identities={SampleEvent: 'sample-event'},
            known_types=[SampleEvent],
        )
        with pytest.raises(ValueError, match="Unknown message type 'nope'"):
            registry.resolve_type('nope')
