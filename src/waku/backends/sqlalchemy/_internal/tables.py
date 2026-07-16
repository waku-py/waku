from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy import MetaData, Table

__all__ = [
    'bind_or_reuse',
]


def bind_or_reuse(metadata: MetaData, table: Table) -> Table:
    """Return the table already bound to ``metadata`` under its name, else bind a copy onto it (idempotent).

    Re-binding the same module-level table definition onto a metadata that already carries it would raise;
    every ``bind_*_tables`` provisioner routes through this one authority so the idempotency rule lives once.
    """
    if table.name in metadata.tables:
        return metadata.tables[table.name]
    return table.to_metadata(metadata)
