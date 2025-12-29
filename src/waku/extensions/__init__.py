from __future__ import annotations

from typing import TYPE_CHECKING

from waku.extensions.protocols import (
    AfterApplicationInit,
    ApplicationExtension,
    ModuleExtension,
    OnApplicationInit,
    OnApplicationShutdown,
    OnModuleConfigure,
    OnModuleDestroy,
    OnModuleInit,
    OnModuleRegistration,
)
from waku.extensions.registry import ExtensionRegistry
from waku.validation import ValidationExtension
from waku.validation.rules import DependenciesAccessibleRule

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = [
    'DEFAULT_EXTENSIONS',
    'AfterApplicationInit',
    'ApplicationExtension',
    'ExtensionRegistry',
    'ModuleExtension',
    'OnApplicationInit',
    'OnApplicationShutdown',
    'OnModuleConfigure',
    'OnModuleDestroy',
    'OnModuleInit',
    'OnModuleRegistration',
]


DEFAULT_EXTENSIONS: Sequence[ApplicationExtension] = (
    ValidationExtension(
        [DependenciesAccessibleRule()],
        strict=True,
    ),
)
