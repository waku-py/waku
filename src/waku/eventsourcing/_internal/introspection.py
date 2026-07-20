from __future__ import annotations

import abc
import inspect
import typing

from typing_extensions import (
    TypeAliasType as BackportTypeAliasType,
    get_original_bases,
)

__all__ = ['is_abstract', 'is_type_alias', 'resolve_generic_args']

# typing_extensions >= 4.16 no longer aliases its ``TypeAliasType`` to the stdlib
# ``typing.TypeAliasType`` on 3.12+, so a native ``type X = ...`` (a ``typing.TypeAliasType``)
# fails an ``isinstance`` check against the backport class alone. Recognise both spellings.
_stdlib_type_alias_type: typing.Final = getattr(typing, 'TypeAliasType', None)
_TYPE_ALIAS_TYPES: typing.Final[tuple[type, ...]] = (
    (BackportTypeAliasType, _stdlib_type_alias_type)
    if _stdlib_type_alias_type is not None
    else (BackportTypeAliasType,)
)


def is_abstract(cls: type) -> bool:
    """Whether *cls* is not yet concrete: abstract methods, direct ABC base, or unbound type parameters."""
    return inspect.isabstract(cls) or abc.ABC in cls.__bases__ or bool(getattr(cls, '__parameters__', ()))


def is_type_alias(obj: object) -> typing.TypeGuard[BackportTypeAliasType]:
    """Whether *obj* is a PEP 695 type alias, whether native (``type X = ...``) or the te backport."""
    return isinstance(obj, _TYPE_ALIAS_TYPES)


def _is_concrete_type_arg(arg: object) -> bool:
    return isinstance(arg, type) or is_type_alias(arg) or typing.get_origin(arg) is not None


def resolve_generic_args(cls: type, base_class: type) -> tuple[object, ...] | None:
    """Walk the MRO and return the first set of concrete generic arguments bound to *base_class*."""
    for klass in cls.__mro__:
        for base in get_original_bases(klass):
            origin = typing.get_origin(base)
            if origin is None or not isinstance(origin, type):  # pragma: no cover
                continue
            try:
                is_match = issubclass(origin, base_class)
            except TypeError:  # pragma: no cover
                continue
            if not is_match:
                continue
            args = typing.get_args(base)
            if args and all(_is_concrete_type_arg(a) for a in args):
                return args
    return None
