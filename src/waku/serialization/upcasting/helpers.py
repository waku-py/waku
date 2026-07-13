from __future__ import annotations

import copy
from typing import TYPE_CHECKING, Any

from waku.serialization.upcasting.fn import FnUpcaster

if TYPE_CHECKING:
    from collections.abc import Callable

    from waku.serialization.upcasting.interfaces import IPayloadUpcaster

__all__ = [
    'add_field',
    'noop',
    'remove_field',
    'rename_field',
    'upcast',
]


def noop(from_version: int) -> IPayloadUpcaster:
    """Build an upcaster that leaves the payload unchanged, bumping only the schema version.

    Args:
        from_version: Schema version this rule upgrades from.

    Returns:
        An upcaster registered for ``from_version``.
    """
    return FnUpcaster(from_version, fn=dict, key=('noop',))


def rename_field(from_version: int, *, old: str, new: str) -> IPayloadUpcaster:
    """Build an upcaster that renames a payload field.

    Args:
        from_version: Schema version this rule upgrades from.
        old: Existing field name to move the value out of.
        new: Field name to move the value into.

    Returns:
        An upcaster registered for ``from_version``.
    """

    def _rename(data: dict[str, Any]) -> dict[str, Any]:
        result = {k: v for k, v in data.items() if k != old}
        if old in data:
            result[new] = data[old]
        return result

    return FnUpcaster(from_version, fn=_rename, key=('rename_field', old, new))


def add_field(from_version: int, *, field: str, default: Any) -> IPayloadUpcaster:
    """Build an upcaster that adds a field with a default when it is absent.

    Args:
        from_version: Schema version this rule upgrades from.
        field: Field name to add.
        default: Value to insert when the field is missing (copied per payload).

    Returns:
        An upcaster registered for ``from_version``.
    """

    def _add(data: dict[str, Any]) -> dict[str, Any]:
        result = dict(data)
        if field not in result:
            result[field] = copy.copy(default)
        return result

    return FnUpcaster(from_version, fn=_add, key=('add_field', field, _hashable_or_repr(default)))


def remove_field(from_version: int, *, field: str) -> IPayloadUpcaster:
    """Build an upcaster that drops a field from the payload.

    Args:
        from_version: Schema version this rule upgrades from.
        field: Field name to remove.

    Returns:
        An upcaster registered for ``from_version``.
    """
    return FnUpcaster(
        from_version,
        fn=lambda data: {k: v for k, v in data.items() if k != field},
        key=('remove_field', field),
    )


def upcast(from_version: int, fn: Callable[[dict[str, Any]], dict[str, Any]]) -> IPayloadUpcaster:
    """Build an upcaster from an arbitrary payload transform.

    Args:
        from_version: Schema version this rule upgrades from.
        fn: Transform mapping the old payload dict to the upgraded one.

    Returns:
        An upcaster registered for ``from_version``.
    """
    return FnUpcaster(from_version, fn=fn)


def _hashable_or_repr(default: Any) -> object:
    try:
        hash(default)
    except TypeError:
        return repr(default)
    return default
