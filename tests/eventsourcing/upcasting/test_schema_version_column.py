from __future__ import annotations

from waku.eventsourcing.store.sqlalchemy.tables import es_events_table


def test_es_events_table_has_schema_version_column() -> None:
    assert 'schema_version' in es_events_table.c
    col = es_events_table.c.schema_version
    assert not col.nullable
    assert col.server_default is not None
