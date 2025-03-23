from dataclasses import dataclass


@dataclass
class User:
    id: str
    name: str
    preferred_language: str = 'en'
