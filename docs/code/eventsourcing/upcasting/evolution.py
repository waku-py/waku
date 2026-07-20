from dataclasses import dataclass

from waku.messages import IEvent
from waku.eventsourcing import EventType
from waku.serialization import add_field, rename_field


@dataclass(frozen=True, kw_only=True)
class AccountOpened(IEvent):
    account_id: str
    owner_name: str
    currency: str


account_opened_type = EventType(
    AccountOpened,
    name='AccountOpened',
    version=3,
    upcasters=[
        rename_field(from_version=1, old='owner', new='owner_name'),
        add_field(from_version=2, field='currency', default='USD'),
    ],
)
