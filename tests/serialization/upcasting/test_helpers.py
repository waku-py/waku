from __future__ import annotations

from waku.serialization.upcasting.helpers import add_field, noop, remove_field, rename_field, upcast


class TestNoop:
    @staticmethod
    def test_returns_data_unchanged() -> None:
        result = noop(from_version=1).upcast({'field': 'value'})
        assert result == {'field': 'value'}

    @staticmethod
    def test_does_not_mutate_input() -> None:
        data = {'field': 'value'}
        noop(from_version=1).upcast(data)
        assert data == {'field': 'value'}

    @staticmethod
    def test_stores_from_version() -> None:
        assert noop(from_version=5).from_version == 5


class TestRenameField:
    @staticmethod
    def test_renames_field() -> None:
        result = rename_field(from_version=1, old='old_name', new='new_name').upcast({'old_name': 'value', 'other': 42})
        assert result == {'new_name': 'value', 'other': 42}

    @staticmethod
    def test_old_key_removed() -> None:
        result = rename_field(from_version=1, old='old_name', new='new_name').upcast({'old_name': 'value'})
        assert 'old_name' not in result

    @staticmethod
    def test_missing_old_key_is_noop() -> None:
        result = rename_field(from_version=1, old='old_name', new='new_name').upcast({'other': 42})
        assert result == {'other': 42}

    @staticmethod
    def test_does_not_mutate_input() -> None:
        data = {'old_name': 'value'}
        rename_field(from_version=1, old='old_name', new='new_name').upcast(data)
        assert data == {'old_name': 'value'}


class TestAddField:
    @staticmethod
    def test_adds_field_with_default() -> None:
        result = add_field(from_version=1, field='new', default='hello').upcast({'existing': 1})
        assert result == {'existing': 1, 'new': 'hello'}

    @staticmethod
    def test_does_not_overwrite_existing() -> None:
        result = add_field(from_version=1, field='field', default='default').upcast({'field': 'original'})
        assert result == {'field': 'original'}

    @staticmethod
    def test_does_not_mutate_input() -> None:
        data = {'existing': 1}
        add_field(from_version=1, field='new', default=0).upcast(data)
        assert data == {'existing': 1}

    @staticmethod
    def test_mutable_default_not_shared() -> None:
        upcaster = add_field(from_version=1, field='tags', default=[])
        result_a = upcaster.upcast({'x': 1})
        result_b = upcaster.upcast({'x': 2})
        assert result_a['tags'] is not result_b['tags']


class TestRemoveField:
    @staticmethod
    def test_removes_field() -> None:
        result = remove_field(from_version=1, field='drop').upcast({'keep': 1, 'drop': 2})
        assert result == {'keep': 1}

    @staticmethod
    def test_missing_field_is_noop() -> None:
        result = remove_field(from_version=1, field='drop').upcast({'keep': 1})
        assert result == {'keep': 1}

    @staticmethod
    def test_does_not_mutate_input() -> None:
        data = {'keep': 1, 'drop': 2}
        remove_field(from_version=1, field='drop').upcast(data)
        assert data == {'keep': 1, 'drop': 2}


class TestUpcast:
    @staticmethod
    def test_applies_custom_function() -> None:
        result = upcast(from_version=1, fn=lambda d: {**d, 'total': d['subtotal'] + d['tax']}).upcast({
            'subtotal': 10,
            'tax': 2,
        })
        assert result == {'subtotal': 10, 'tax': 2, 'total': 12}

    @staticmethod
    def test_stores_from_version() -> None:
        assert upcast(from_version=3, fn=lambda d: d).from_version == 3
