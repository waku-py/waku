from app.settings import AppSettings
from app.modules.greetings.models import Greeting


class GreetingService:
    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings
        self.greetings: dict[str, Greeting] = {
            'en': Greeting(language='en', template='Hello, {}!'),
            'es': Greeting(language='es', template='¡Hola, {}!'),
            'fr': Greeting(language='fr', template='Bonjour, {}!'),
        }

    def get_greeting(self, language: str = 'en') -> Greeting:
        greeting = self.greetings.get(language)
        if greeting is not None:
            return greeting
        if not self.settings.debug:
            msg = f'Unsupported language: {language!r}'
            raise ValueError(msg)
        return self.greetings['en']

    def greet(self, name: str, language: str = 'en') -> str:
        greeting = self.get_greeting(language)
        return greeting.template.format(name)

    def available_languages(self) -> list[str]:
        return list(self.greetings.keys())
