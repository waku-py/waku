from collections.abc import Callable, Sequence
from typing import Any

import pytest
from dishka import Provider

from waku.di import (
    ActivationContext,
    ConditionalProvider,
    contextual,
    many,
    object_,
    scoped,
    singleton,
    transient,
)

from tests.data import A, B, Service


def always(_: ActivationContext) -> bool:
    return True


class TestProviderFunctionsWithoutWhen:
    @staticmethod
    @pytest.mark.parametrize(
        'provider_func',
        [singleton, scoped, transient, contextual],
        ids=['singleton', 'scoped', 'transient', 'contextual'],
    )
    def test_simple_provider_returns_provider_instance(
        provider_func: Callable[..., Any],
    ) -> None:
        result = provider_func(Service)

        assert isinstance(result, Provider)

    @staticmethod
    @pytest.mark.parametrize(
        'provider_func',
        [singleton, scoped, transient],
        ids=['singleton', 'scoped', 'transient'],
    )
    def test_interface_implementation_returns_provider(
        provider_func: Callable[..., Any],
    ) -> None:
        result = provider_func(A, B)

        assert isinstance(result, Provider)

    @staticmethod
    def test_object_returns_provider() -> None:
        instance = Service()

        result = object_(instance, provided_type=Service)

        assert isinstance(result, Provider)

    @staticmethod
    def test_many_returns_provider() -> None:
        result = many(Service, Service)

        assert isinstance(result, Provider)


class TestProviderFunctionsWithWhen:
    @staticmethod
    @pytest.mark.parametrize(
        'provider_func',
        [singleton, scoped, transient, contextual],
        ids=['singleton', 'scoped', 'transient', 'contextual'],
    )
    def test_simple_provider_returns_conditional_provider(
        provider_func: Callable[..., Any],
    ) -> None:
        result = provider_func(Service, when=always)

        assert isinstance(result, ConditionalProvider)
        assert isinstance(result.provider, Provider)
        assert result.provided_type is Service

    @staticmethod
    @pytest.mark.parametrize(
        'provider_func',
        [singleton, scoped, transient],
        ids=['singleton', 'scoped', 'transient'],
    )
    def test_interface_implementation_returns_conditional_provider(
        provider_func: Callable[..., Any],
    ) -> None:
        result = provider_func(A, B, when=always)

        assert isinstance(result, ConditionalProvider)
        assert result.provided_type is A

    @staticmethod
    def test_object_returns_conditional_provider() -> None:
        instance = Service()

        result = object_(instance, provided_type=Service, when=always)

        assert isinstance(result, ConditionalProvider)
        assert result.provided_type is Service

    @staticmethod
    def test_many_returns_conditional_provider() -> None:
        result = many(Service, Service, when=always)

        assert isinstance(result, ConditionalProvider)
        assert result.provided_type == Sequence[Service]


class TestPredicateAttachment:
    @staticmethod
    @pytest.mark.parametrize(
        ('provider_func', 'args'),
        [
            (singleton, (Service,)),
            (scoped, (Service,)),
            (transient, (Service,)),
            (contextual, (Service,)),
        ],
        ids=['singleton', 'scoped', 'transient', 'contextual'],
    )
    def test_predicate_is_correctly_attached(
        provider_func: Callable[..., Any],
        args: tuple[Any, ...],
    ) -> None:
        def custom_predicate(_: ActivationContext) -> bool:
            return True

        result = provider_func(*args, when=custom_predicate)

        assert isinstance(result, ConditionalProvider)
        assert result.when is custom_predicate
