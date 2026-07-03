from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from waku.messaging.contracts.message import IMessage
from waku.messaging.observability.audit import Audit


@dataclass
class Bad(IMessage):
    account_id: Annotated[str, Audit()] = ''
    ref: DoesNotExistAtRuntime = None  # type: ignore[name-defined]  # noqa: F821 -- intentional NameError source
