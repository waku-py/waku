from app.modules.users.models import User
from app.modules.users.repositories import IUserRepository


class UserService:
    def __init__(self, repo: IUserRepository) -> None:
        self._repo = repo

    def get_user(self, user_id: str) -> User | None:
        return self._repo.get(user_id)
