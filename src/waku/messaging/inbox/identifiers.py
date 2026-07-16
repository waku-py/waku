from typing import NewType

__all__ = [
    'EndpointUri',
    'HandlerDestination',
]

# Zero-runtime-cost guards for the two persisted inbox string identities. NewType keeps database
# values and wire bytes identical to str while preventing source URI and handler destination from
# being swapped at write-side boundaries.
EndpointUri = NewType('EndpointUri', str)
HandlerDestination = NewType('HandlerDestination', str)
