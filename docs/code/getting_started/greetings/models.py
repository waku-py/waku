from dataclasses import dataclass


@dataclass
class Greeting:
    language: str
    template: str
