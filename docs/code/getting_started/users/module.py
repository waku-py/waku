from waku import module
from waku.di import scoped

from app.modules.users.services import UserService


@module(
    providers=[scoped(UserService)],
    exports=[UserService],
)
class UserModule:
    pass
