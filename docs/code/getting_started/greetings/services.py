from app.config import AppConfig
from app.modules.greetings.models import Greeting


class GreetingService:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.greetings: dict[str, Greeting] = {
            'en': Greeting(language='en', template='Hello, {}!'),
            'es': Greeting(language='es', template='¡Hola, {}!'),
            'fr': Greeting(language='fr', template='Bonjour, {}!'),
        }

    def get_greeting(self, language: str = 'en') -> Greeting:
        # If in debug mode and language not found, return default
        if self.config.debug and language not in self.greetings:
            return self.greetings['en']
        return self.greetings.get(language, self.greetings['en'])

    def greet(self, name: str, language: str = 'en') -> str:
        greeting = self.get_greeting(language)
        return greeting.template.format(name)

    def available_languages(self) -> list[str]:
        return list(self.greetings.keys())
