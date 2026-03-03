from typing import Protocol

from app.modules.users.models import User


class IUserRepository(Protocol):
    def get(self, user_id: str) -> User | None: ...


class InMemoryUserRepository(IUserRepository):
    def __init__(self) -> None:
        self._users: dict[str, User] = {
            '1': User(id='1', name='Alice', preferred_language='en'),
            '2': User(id='2', name='Bob', preferred_language='fr'),
            '3': User(id='3', name='Carlos', preferred_language='es'),
        }

    def get(self, user_id: str) -> User | None:
        return self._users.get(user_id)
