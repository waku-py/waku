from __future__ import annotations

import pytest

from waku.eventsourcing.exceptions import UpcasterChainError
from waku.eventsourcing.upcasting.chain import UpcasterChain
from waku.eventsourcing.upcasting.helpers import add_field, remove_field, rename_field


class TestEmptyChain:
    @staticmethod
    def test_returns_data_unchanged() -> None:
        chain = UpcasterChain({})
        result = chain.upcast('OrderCreated', {'field': 'value'}, schema_version=1)
        assert result == {'field': 'value'}

    @staticmethod
    def test_unknown_event_type_returns_data_unchanged() -> None:
        chain = UpcasterChain({'OtherEvent': [add_field(from_version=1, field='x', default=0)]})
        result = chain.upcast('OrderCreated', {'field': 'value'}, schema_version=1)
        assert result == {'field': 'value'}


class TestSingleUpcaster:
    @staticmethod
    def test_transforms_data() -> None:
        chain = UpcasterChain({
            'OrderCreated': [rename_field(from_version=1, old='name', new='full_name')],
        })
        result = chain.upcast('OrderCreated', {'name': 'Alice'}, schema_version=1)
        assert result == {'full_name': 'Alice'}

    @staticmethod
    def test_skips_when_version_already_past() -> None:
        chain = UpcasterChain({
            'OrderCreated': [rename_field(from_version=1, old='name', new='full_name')],
        })
        result = chain.upcast('OrderCreated', {'full_name': 'Alice'}, schema_version=2)
        assert result == {'full_name': 'Alice'}


class TestChainedUpcasters:
    @staticmethod
    def test_applies_sequentially() -> None:
        chain = UpcasterChain({
            'OrderCreated': [
                rename_field(from_version=1, old='name', new='full_name'),
                add_field(from_version=2, field='email', default=''),
            ],
        })
        result = chain.upcast('OrderCreated', {'name': 'Alice'}, schema_version=1)
        assert result == {'full_name': 'Alice', 'email': ''}

    @staticmethod
    def test_starts_from_stored_version() -> None:
        chain = UpcasterChain({
            'OrderCreated': [
                rename_field(from_version=1, old='name', new='full_name'),
                add_field(from_version=2, field='email', default=''),
            ],
        })
        result = chain.upcast('OrderCreated', {'full_name': 'Alice'}, schema_version=2)
        assert result == {'full_name': 'Alice', 'email': ''}


class TestSparseChain:
    @staticmethod
    def test_skips_gaps() -> None:
        chain = UpcasterChain({
            'OrderCreated': [
                rename_field(from_version=3, old='name', new='full_name'),
                add_field(from_version=7, field='email', default=''),
                remove_field(from_version=12, field='legacy_id'),
            ],
        })
        data = {'name': 'Alice', 'legacy_id': 'old-123'}
        result = chain.upcast('OrderCreated', data, schema_version=1)
        assert result == {'full_name': 'Alice', 'email': ''}

    @staticmethod
    def test_version_past_all_upcasters_returns_unchanged() -> None:
        chain = UpcasterChain({
            'OrderCreated': [
                rename_field(from_version=3, old='name', new='full_name'),
            ],
        })
        result = chain.upcast('OrderCreated', {'full_name': 'Alice'}, schema_version=13)
        assert result == {'full_name': 'Alice'}


class TestValidation:
    @staticmethod
    def test_duplicate_from_version_raises() -> None:
        with pytest.raises(UpcasterChainError, match=r'Duplicate upcaster.*OrderCreated.*from_version 1'):
            UpcasterChain({
                'OrderCreated': [
                    add_field(from_version=1, field='a', default=0),
                    add_field(from_version=1, field='b', default=0),
                ],
            })

    @staticmethod
    def test_from_version_less_than_one_raises() -> None:
        with pytest.raises(UpcasterChainError, match=r'Invalid from_version 0.*OrderCreated.*must be >= 1'):
            UpcasterChain({
                'OrderCreated': [
                    add_field(from_version=0, field='a', default=0),
                ],
            })


class TestMultipleEventTypes:
    @staticmethod
    def test_upcasts_independently() -> None:
        chain = UpcasterChain({
            'OrderCreated': [rename_field(from_version=1, old='name', new='full_name')],
            'ItemAdded': [add_field(from_version=1, field='quantity', default=1)],
        })

        order_result = chain.upcast('OrderCreated', {'name': 'Alice'}, schema_version=1)
        assert order_result == {'full_name': 'Alice'}

        item_result = chain.upcast('ItemAdded', {'item': 'Widget'}, schema_version=1)
        assert item_result == {'item': 'Widget', 'quantity': 1}
