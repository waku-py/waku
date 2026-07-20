from dataclasses import dataclass
from uuid import UUID, uuid4

from adaptix import loader

from waku._internal.retort import default_retort
from waku.messages import MessageIdentity
from waku.serialization import FnUpcaster, UpcasterChain
from waku.serialization.codec import PayloadCodec


@dataclass(frozen=True)
class Thing:
    id: UUID
    label: str


def test_encode_then_load_round_trips() -> None:
    codec = PayloadCodec(default_retort, UpcasterChain({}))
    value = Thing(id=uuid4(), label='a')
    data = codec.encode(value, Thing)
    assert codec.load(data, Thing) == value


def test_decode_applies_chain_then_loads() -> None:
    chain = UpcasterChain({'thing': [FnUpcaster(from_version=1, fn=lambda d: {**d, 'label': 'upcasted'})]})
    codec = PayloadCodec(default_retort, chain)
    raw = {'id': str(uuid4())}
    loaded = codec.decode(raw | {'label': 'old'}, Thing, MessageIdentity(name='thing', version=1))
    assert loaded.label == 'upcasted'


def test_extend_applies_recipe_and_preserves_chain() -> None:
    chain = UpcasterChain({'thing': [FnUpcaster(from_version=1, fn=lambda d: {**d, 'label': 'x'})]})
    codec = PayloadCodec(default_retort, chain).extend(
        recipe=[loader(Thing, lambda data: Thing(id=UUID(data['id']), label=data['label'].upper()))],
    )
    # chain (preserved) sets label='x'; the extended recipe (applied) uppercases it to 'X'
    loaded = codec.decode({'id': str(uuid4()), 'label': 'old'}, Thing, MessageIdentity(name='thing', version=1))
    assert loaded.label == 'X'
