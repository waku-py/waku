from typing import Any, TypeVar, cast

from adaptix import Retort

from waku.messages import MessageIdentity
from waku.serialization.upcasting.chain import UpcasterChain

__all__ = ['PayloadCodec']

_T = TypeVar('_T')


class PayloadCodec:
    __slots__ = ('_chain', '_retort')

    def __init__(self, retort: Retort, chain: UpcasterChain) -> None:
        self._retort = retort
        self._chain = chain

    def encode(self, value: object, value_type: type[Any]) -> dict[str, Any]:
        return cast('dict[str, Any]', self._retort.dump(value, value_type))

    def decode(self, data: dict[str, Any], target: type[_T], identity: MessageIdentity) -> _T:
        upcasted = self._chain.upcast(identity.name, data, identity.version)
        return self._retort.load(upcasted, target)

    def load(self, data: dict[str, Any], target: type[_T]) -> _T:
        return self._retort.load(data, target)

    def extend(self, *, recipe: list[Any]) -> 'PayloadCodec':
        return PayloadCodec(self._retort.extend(recipe=recipe), self._chain)
