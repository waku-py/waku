from dataclasses import dataclass

from waku.cqrs import INotification
from waku.eventsourcing import EventType, add_field, rename_field


@dataclass(frozen=True, kw_only=True)
class AccountOpened(INotification):
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
