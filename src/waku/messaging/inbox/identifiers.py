from typing import NewType

__all__ = [
    'EndpointUri',
    'HandlerDestination',
]

# Zero-runtime-cost guards for persisted string identities — a first-class column that is also a
# write-side key. NewType keeps database values and wire bytes identical to str while preventing
# these identities from being swapped. Same convention guards GroupId (waku.messaging.sequence).
EndpointUri = NewType('EndpointUri', str)
HandlerDestination = NewType('HandlerDestination', str)
