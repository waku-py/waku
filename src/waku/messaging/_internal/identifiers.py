from typing import NewType

__all__ = [
    'EndpointUri',
    'GroupId',
    'HandlerDestination',
]

# Zero-runtime-cost static type-guards for the three string identities that travel on-wire / through the
# DB. `NewType` is transparent at runtime — `HandlerDestination('x') == 'x'` and serializes as the plain
# `str` — so wrapping leaves the composite-PK column values and on-wire bytes byte-identical while letting
# the type checker reject mixing an endpoint URI, a handler-FQN dedup discriminator, or a partition key.
# The guard is applied at the write-side seams (`handler_destination()`, `InboxEntry` fields, `allocate`);
# read-back paths intentionally stay bare `str`, so this protects the direction where confusion would bite.
EndpointUri = NewType('EndpointUri', str)
HandlerDestination = NewType('HandlerDestination', str)
GroupId = NewType('GroupId', str)
