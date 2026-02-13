from __future__ import annotations

from waku.eventsourcing.upcasting.helpers import add_field, noop, remove_field, rename_field, upcast
from waku.eventsourcing.upcasting.interfaces import IEventUpcaster


class TestNoop:
    @staticmethod
    def test_returns_data_unchanged() -> None:
        data = {'field': 'value'}
        result = noop(from_version=1).upcast(data)
        assert result == {'field': 'value'}

    @staticmethod
    def test_returns_new_dict() -> None:
        data = {'field': 'value'}
        result = noop(from_version=1).upcast(data)
        assert result is not data

    @staticmethod
    def test_stores_from_version() -> None:
        assert noop(from_version=5).from_version == 5

    @staticmethod
    def test_is_event_upcaster() -> None:
        assert isinstance(noop(from_version=1), IEventUpcaster)


class TestRenameField:
    @staticmethod
    def test_renames_field() -> None:
        data = {'old_name': 'value', 'other': 42}
        result = rename_field(from_version=1, old='old_name', new='new_name').upcast(data)
        assert result == {'new_name': 'value', 'other': 42}

    @staticmethod
    def test_old_key_removed() -> None:
        data = {'old_name': 'value'}
        result = rename_field(from_version=1, old='old_name', new='new_name').upcast(data)
        assert 'old_name' not in result

    @staticmethod
    def test_missing_old_key_is_noop() -> None:
        data = {'other': 42}
        result = rename_field(from_version=1, old='old_name', new='new_name').upcast(data)
        assert result == {'other': 42}

    @staticmethod
    def test_returns_new_dict() -> None:
        data = {'old_name': 'value'}
        result = rename_field(from_version=1, old='old_name', new='new_name').upcast(data)
        assert result is not data


class TestAddField:
    @staticmethod
    def test_adds_field_with_default() -> None:
        data = {'existing': 1}
        result = add_field(from_version=1, field='new', default='hello').upcast(data)
        assert result == {'existing': 1, 'new': 'hello'}

    @staticmethod
    def test_does_not_overwrite_existing() -> None:
        data = {'field': 'original'}
        result = add_field(from_version=1, field='field', default='default').upcast(data)
        assert result == {'field': 'original'}

    @staticmethod
    def test_returns_new_dict() -> None:
        data = {'existing': 1}
        result = add_field(from_version=1, field='new', default=0).upcast(data)
        assert result is not data

    @staticmethod
    def test_mutable_default_not_shared() -> None:
        upcaster = add_field(from_version=1, field='tags', default=[])
        result_a = upcaster.upcast({'x': 1})
        result_b = upcaster.upcast({'x': 2})
        assert result_a['tags'] is not result_b['tags']


class TestRemoveField:
    @staticmethod
    def test_removes_field() -> None:
        data = {'keep': 1, 'drop': 2}
        result = remove_field(from_version=1, field='drop').upcast(data)
        assert result == {'keep': 1}

    @staticmethod
    def test_missing_field_is_noop() -> None:
        data = {'keep': 1}
        result = remove_field(from_version=1, field='drop').upcast(data)
        assert result == {'keep': 1}

    @staticmethod
    def test_returns_new_dict() -> None:
        data = {'field': 1}
        result = remove_field(from_version=1, field='field').upcast(data)
        assert result is not data


class TestUpcast:
    @staticmethod
    def test_applies_custom_function() -> None:
        data = {'subtotal': 10, 'tax': 2}
        result = upcast(
            from_version=1,
            fn=lambda d: {**d, 'total': d['subtotal'] + d['tax']},
        ).upcast(data)
        assert result == {'subtotal': 10, 'tax': 2, 'total': 12}

    @staticmethod
    def test_stores_from_version() -> None:
        assert upcast(from_version=3, fn=lambda d: d).from_version == 3

    @staticmethod
    def test_returns_new_dict() -> None:
        data = {'a': 1}
        result = upcast(from_version=1, fn=lambda d: {**d}).upcast(data)
        assert result is not data
