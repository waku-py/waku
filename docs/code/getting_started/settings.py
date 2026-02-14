from dataclasses import dataclass
from typing import Literal

from waku import DynamicModule, module
from waku.di import object_

Environment = Literal['dev', 'prod']


# You may consider using `pydantic-settings` or similar libs for settings management
@dataclass(kw_only=True)
class AppSettings:
    environment: Environment
    debug: bool


@module(is_global=True)
class ConfigModule:
    @classmethod
    def register(cls, env: Environment) -> DynamicModule:
        settings = AppSettings(
            environment=env,
            debug=env == 'dev',
        )
        return DynamicModule(
            parent_module=cls,
            providers=[object_(settings)],
        )
