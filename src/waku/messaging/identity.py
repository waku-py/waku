from __future__ import annotations

from typing import TYPE_CHECKING

from waku.exceptions import ImproperlyConfiguredError
from waku.messaging.contracts.identity import MessageIdentity

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from waku.messaging.contracts.message import IMessage

__all__ = [
    'MessageTypeRegistry',
    'resolve_message_identity',
]


def _fqn(cls: type) -> str:
    return f'{cls.__module__}.{cls.__qualname__}'


def _own_class_identity(cls: type) -> str | MessageIdentity | None:
    """Own-class ``message_identity`` — read via ``__dict__`` (no MRO walk).

    Wire identity does not inherit, unlike handler policies (``getattr``/MRO).

    Raises:
        ImproperlyConfiguredError: if present but not ``str``/``MessageIdentity``
            (e.g. declared without ``ClassVar`` → a slot descriptor, not a value).
    """
    own = cls.__dict__.get('message_identity')
    if own is None:
        return None
    if not isinstance(own, (str, MessageIdentity)):
        msg = (
            f'{cls.__qualname__}.message_identity must be '
            f'ClassVar[str | MessageIdentity], got {type(own).__name__} — '
            f'did you forget the ClassVar annotation?'
        )
        raise ImproperlyConfiguredError(msg)
    return own


def resolve_message_identity(
    msg_type: type[IMessage],
    config_identities: Mapping[type[IMessage], str | MessageIdentity],
) -> str:
    """Single source of truth for a message type's wire name.

    Own-class ClassVar (see ``_own_class_identity``) → ``config_identities``
    override (third-party types) → FQN fallback.
    """
    own = _own_class_identity(msg_type)
    if own:  # empty '' is a mis-set, not an opt-in -> fall through to FQN
        return str(own)
    override = config_identities.get(msg_type)
    if override:
        return str(override)
    return _fqn(msg_type)


class MessageTypeRegistry:
    __slots__ = ('_identities', '_name_to_type', '_type_to_name')

    def __init__(
        self,
        identities: Mapping[type[IMessage], str | MessageIdentity],
        known_types: Iterable[type[IMessage]],
    ) -> None:
        self._identities = identities
        self._type_to_name: dict[type[IMessage], str] = {}
        self._name_to_type: dict[str, type[IMessage]] = {}
        # Build bidirectional map for known (handler-bound) types so
        # deserialization can map a wire name back to a type.
        for cls in known_types:
            self._register(cls, resolve_message_identity(cls, identities))

    def _register(self, cls: type[IMessage], name: str) -> None:
        existing = self._name_to_type.get(name)
        if existing is not None and existing is not cls:
            msg = f'Duplicate message identity {name!r}: {existing.__qualname__} and {cls.__qualname__}'
            raise ImproperlyConfiguredError(msg)
        self._type_to_name[cls] = name
        self._name_to_type[name] = cls

    def resolve_name(self, cls: type[IMessage]) -> str:
        cached = self._type_to_name.get(cls)
        if cached is not None:
            return cached
        # Not pre-registered (e.g. a send-only message) — resolve on the fly so
        # the ClassVar/config override is still honored, FQN as last resort.
        return resolve_message_identity(cls, self._identities)

    def resolve_type(self, name: str) -> type[IMessage]:
        cls = self._name_to_type.get(name)
        if cls is not None:
            return cls
        registered = sorted(self._name_to_type)
        msg = f'Unknown message type {name!r}. Registered types: {registered}'
        raise ValueError(msg)
