from app.modules.users.models import User


class UserService:
    def __init__(self) -> None:
        # Mock database
        self.users: dict[str, User] = {
            '1': User(id='1', name='Alice', preferred_language='en'),
            '2': User(id='2', name='Bob', preferred_language='fr'),
            '3': User(id='3', name='Carlos', preferred_language='es'),
        }

    def get_user(self, user_id: str) -> User | None:
        return self.users.get(user_id)
