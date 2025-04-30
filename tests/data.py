"""Common test data classes used across tests."""

from dataclasses import dataclass
from typing import NewType

from dishka.provider import BaseProvider

from waku import Module
from waku.extensions import OnModuleConfigure, OnModuleDestroy, OnModuleInit
from waku.modules import ModuleMetadata


# Simple test services
@dataclass
class Service:
    """A simple service for testing."""


@dataclass
class DependentService:
    """A service that depends on another service."""

    service: Service


@dataclass
class RequestContext:
    """A simple request context for testing."""

    user_id: int


@dataclass
class UserService:
    """A user service for testing."""

    user_id: int


# Simple A, B, C services commonly used in dependency tests
@dataclass
class A:
    """Service A for testing dependencies."""


AAliasType = NewType('AAliasType', A)


@dataclass
class B:
    """Service B for testing dependencies."""

    a: A


@dataclass
class C:
    """Service C for testing dependencies."""


@dataclass
class X:
    """Service X for testing dependencies."""


@dataclass
class Y:
    """Service Y for testing dependencies."""


@dataclass
class Z:
    """Service Z for testing dependencies."""

    x: X
    y: Y


# Common module extensions
class OnInitExt(OnModuleInit):
    """Extension that tracks module initialization."""

    def __init__(self, calls: list[tuple[type, type]]) -> None:
        self.calls = calls

    async def on_module_init(self, module: Module) -> None:
        self.calls.append((module.target, type(self)))


class OnDestroyExt(OnModuleDestroy):
    """Extension that tracks module destruction."""

    def __init__(self, calls: list[tuple[type, type]]) -> None:
        self.calls = calls

    async def on_module_destroy(self, module: Module) -> None:
        self.calls.append((module.target, type(self)))


class AddDepOnConfigure(OnModuleConfigure):
    """Extension that adds a dependency during module configuration."""

    def __init__(self, provider: BaseProvider) -> None:
        self.provider = provider

    def on_module_configure(self, metadata: ModuleMetadata) -> None:
        metadata.providers.append(self.provider)
