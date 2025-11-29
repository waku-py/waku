import pytest

from waku.di import ActivationContext, Has


class _MockBuilder:
    def __init__(self, registered: set[type] | None = None) -> None:
        self._registered = registered or set()

    def has_active(self, type_: object) -> bool:
        return type_ in self._registered


class TestActivationContextFields:
    @staticmethod
    def test_container_context_field() -> None:
        ctx = ActivationContext(
            container_context={'key': 'value'},
            module_type=object,
            provided_type=str,
            builder=_MockBuilder(),
        )

        assert ctx.container_context == {'key': 'value'}

    @staticmethod
    def test_module_type_field() -> None:
        class MyModule:
            pass

        ctx = ActivationContext(
            container_context={},
            module_type=MyModule,
            provided_type=str,
            builder=_MockBuilder(),
        )

        assert ctx.module_type is MyModule

    @staticmethod
    def test_provided_type_field() -> None:
        ctx = ActivationContext(
            container_context={},
            module_type=object,
            provided_type=int,
            builder=_MockBuilder(),
        )

        assert ctx.provided_type is int

    @staticmethod
    def test_builder_has_active() -> None:
        builder = _MockBuilder(registered={str, int})

        ctx = ActivationContext(
            container_context={},
            module_type=object,
            provided_type=int,
            builder=builder,
        )

        assert ctx.builder.has_active(str) is True
        assert ctx.builder.has_active(float) is False


class TestHasActivator:
    @staticmethod
    @pytest.mark.parametrize(
        ('registered', 'check_type', 'expected'),
        [
            pytest.param({str, int}, str, True, id='type_registered'),
            pytest.param({str}, float, False, id='type_not_registered'),
            pytest.param(set(), str, False, id='empty_registry'),
        ],
    )
    def test_checks_if_type_is_registered(
        registered: set[type],
        check_type: type,
        expected: bool,
    ) -> None:
        builder = _MockBuilder(registered=registered)
        ctx = ActivationContext(
            container_context={},
            module_type=object,
            provided_type=str,
            builder=builder,
        )

        has = Has(check_type)

        assert has(ctx) is expected
