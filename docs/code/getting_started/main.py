import asyncio

from app.application import bootstrap_application
from app.modules.greetings.services import GreetingService
from app.modules.users.services import UserService


async def main() -> None:
    application = bootstrap_application()

    async with application, application.container() as container:
        user_service = await container.get(UserService)
        greeting_service = await container.get(GreetingService)

        for user_id in ['1', '2', '3', '4']:
            user = user_service.get_user(user_id)
            if not user:
                print(f'User {user_id} not found')
                continue
            print(greeting_service.greet(name=user.name, language=user.preferred_language))

        print(f'Available languages: {greeting_service.available_languages()}')


if __name__ == '__main__':
    asyncio.run(main())
