from dataclasses import dataclass
from typing import Protocol

from waku import module
from waku.di import scoped


@dataclass
class User:
    id: str
    name: str


class IUserRepository(Protocol):
    def get(self, user_id: str) -> User | None: ...


class InMemoryUserRepository(IUserRepository):
    def __init__(self) -> None:
        self._storage: dict[str, User] = {
            '1': User(id='1', name='Alice'),
        }

    def get(self, user_id: str) -> User | None:
        return self._storage.get(user_id)


class UserService:
    def __init__(self, repo: IUserRepository) -> None:
        self._repo = repo

    def get_user(self, user_id: str) -> User | None:
        return self._repo.get(user_id)


@module(
    providers=[
        scoped(IUserRepository, InMemoryUserRepository),  # (1)!
        scoped(UserService),  # (2)!
    ],
    exports=[UserService],  # (3)!
)
class UserModule:
    pass
