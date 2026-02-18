from __future__ import annotations

import typing

from typing_extensions import get_original_bases

__all__ = ['resolve_generic_args']


def resolve_generic_args(cls: type, base_class: type) -> tuple[type, ...] | None:
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
            if args and isinstance(args[0], type):
                return args
    return None
