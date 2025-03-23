from waku import module
from waku.di import Scoped

from app.modules.users.services import UserService


@module(
    providers=[Scoped(UserService)],
    exports=[UserService],
)
class UserModule:
    pass
