from __future__ import annotations

from functools import partial

from sqlalchemy import Enum

__all__ = ['EnumFromValues']


# Store enum values as strings (e.g. 'INCOMING'). Use for StrEnum-backed columns.
EnumFromValues = partial(
    Enum,
    native_enum=False,
    values_callable=lambda enum_type: [el.value for el in enum_type],
    create_constraint=False,
)
