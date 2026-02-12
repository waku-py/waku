from collections.abc import Callable
from typing import Any

import pytest
from dishka import Marker, Provider

from waku.di import (
    Has,
    contextual,
    many,
    object_,
    scoped,
    singleton,
    transient,
)

from tests.data import A, B, Service

ALWAYS = Marker('always')


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
        [singleton, scoped, transient],
        ids=['singleton', 'scoped', 'transient'],
    )
    def test_simple_provider_with_when_returns_provider(
        provider_func: Callable[..., Any],
    ) -> None:
        result = provider_func(Service, when=ALWAYS)

        assert isinstance(result, Provider)

    @staticmethod
    @pytest.mark.parametrize(
        'provider_func',
        [singleton, scoped, transient],
        ids=['singleton', 'scoped', 'transient'],
    )
    def test_interface_implementation_with_when_returns_provider(
        provider_func: Callable[..., Any],
    ) -> None:
        result = provider_func(A, B, when=ALWAYS)

        assert isinstance(result, Provider)

    @staticmethod
    def test_object_with_when_returns_provider() -> None:
        instance = Service()

        result = object_(instance, provided_type=Service, when=ALWAYS)

        assert isinstance(result, Provider)

    @staticmethod
    def test_many_with_when_returns_provider() -> None:
        result = many(Service, Service, when=ALWAYS)

        assert isinstance(result, Provider)


class TestMarkerPassthrough:
    @staticmethod
    def test_when_marker_is_set_on_factories() -> None:
        marker = Marker('test')
        result = singleton(Service, when=marker)

        for factory in result.factories:
            assert factory.when_active == marker

    @staticmethod
    def test_has_marker_is_set_on_factories() -> None:
        marker = Has(A)
        result = scoped(Service, when=marker)

        for factory in result.factories:
            assert factory.when_active == marker

    @staticmethod
    def test_negated_marker_is_set_on_factories() -> None:
        marker = ~Marker('test')
        result = transient(Service, when=marker)

        for factory in result.factories:
            assert factory.when_active == marker

    @staticmethod
    def test_when_marker_is_set_on_many_factories() -> None:
        marker = Marker('test')
        result = many(A, B, when=marker)

        for factory in result.factories:
            assert factory.when_active == marker
