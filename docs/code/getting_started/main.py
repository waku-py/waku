import asyncio

from waku.di import Injected, inject

from app.application import bootstrap_application
from app.modules.users.services import UserService
from app.modules.greetings.services import GreetingService


@inject
async def greet_user_by_id(
    user_id: str,
    user_service: Injected[UserService],
    greeting_service: Injected[GreetingService],
) -> str:
    user = user_service.get_user(user_id)
    if not user:
        return f'User {user_id} not found'

    return greeting_service.greet(name=user.name, language=user.preferred_language)


async def main() -> None:
    application = bootstrap_application()

    async with application, application.container() as container:
        # Greet different users
        for user_id in ['1', '2', '3', '4']:  # '4' doesn't exist
            greeting = await greet_user_by_id(user_id)  # type: ignore[call-arg]
            print(greeting)

        # Get service directly for demonstration
        greeting_service = await container.get(GreetingService)
        print(f'Available languages: {greeting_service.available_languages()}')


if __name__ == '__main__':
    asyncio.run(main())
