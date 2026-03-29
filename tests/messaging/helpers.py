from __future__ import annotations

from waku.messaging.transport.serialization import JsonEnvelopeSerializer


def make_serializer(*types: type) -> JsonEnvelopeSerializer:
    registry = {f'{t.__module__}.{t.__qualname__}': t for t in types}
    return JsonEnvelopeSerializer(type_registry=registry)
