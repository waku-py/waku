from __future__ import annotations

import abc
import inspect
import typing

from typing_extensions import TypeAliasType, get_original_bases

__all__ = ['is_abstract', 'resolve_generic_args']


def is_abstract(cls: type) -> bool:
    return inspect.isabstract(cls) or abc.ABC in cls.__bases__


def _is_concrete_type_arg(arg: object) -> bool:
    return isinstance(arg, (type, TypeAliasType)) or typing.get_origin(arg) is not None


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
