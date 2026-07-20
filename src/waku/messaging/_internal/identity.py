from __future__ import annotations

from typing import TYPE_CHECKING

from waku.exceptions import ImproperlyConfiguredError
from waku.messages import MessageIdentity

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from waku.messages import IMessage

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
            (e.g. declared without ``ClassVar`` → a slot descriptor, not a value),
            or set to an empty string (a typo, not an opt-out).
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
    if isinstance(own, str) and not own:
        msg = f'{cls.__qualname__}.message_identity must not be an empty string — omit it to use the FQN fallback'
        raise ImproperlyConfiguredError(msg)
    return own


def resolve_message_identity(
    msg_type: type[IMessage],
    config_identities: Mapping[type[IMessage], str | MessageIdentity],
) -> MessageIdentity:
    """Single source of truth for a message type's wire identity.

    Own-class ClassVar (see ``_own_class_identity``) → ``config_identities``
    override (third-party types) → FQN fallback.

    Raises:
        ImproperlyConfiguredError: if the own-class ClassVar is malformed, or either
            the ClassVar or the config override is an empty string.
    """
    own = _own_class_identity(msg_type)
    if own is not None:
        return own if isinstance(own, MessageIdentity) else MessageIdentity(name=own)
    override = config_identities.get(msg_type)
    if override is not None:
        if isinstance(override, str) and not override:
            msg = f'message_identity override for {msg_type.__qualname__} must not be an empty string'
            raise ImproperlyConfiguredError(msg)
        return override if isinstance(override, MessageIdentity) else MessageIdentity(name=override)
    return MessageIdentity(name=_fqn(msg_type))


class MessageTypeRegistry:
    __slots__ = ('_identities', '_name_to_type', '_type_to_identity')

    def __init__(
        self,
        identities: Mapping[type[IMessage], str | MessageIdentity],
        known_types: Iterable[type[IMessage]],
    ) -> None:
        self._identities = identities
        self._type_to_identity: dict[type[IMessage], MessageIdentity] = {}
        self._name_to_type: dict[str, type[IMessage]] = {}
        # Build bidirectional map for known (handler-bound) types so
        # deserialization can map a wire name back to a type.
        for cls in known_types:
            self._register(cls, resolve_message_identity(cls, identities))

    def _register(self, cls: type[IMessage], identity: MessageIdentity) -> None:
        existing = self._name_to_type.get(identity.name)
        if existing is not None and existing is not cls:
            msg = f'duplicate message identity {identity.name!r}: {existing.__qualname__} and {cls.__qualname__}'
            raise ImproperlyConfiguredError(msg)
        self._type_to_identity[cls] = identity
        self._name_to_type[identity.name] = cls

    def resolve_identity(self, cls: type[IMessage]) -> MessageIdentity:
        cached = self._type_to_identity.get(cls)
        if cached is not None:
            return cached
        # Not pre-registered (e.g. a send-only message) — resolve on the fly so
        # the ClassVar/config override is still honored, FQN as last resort.
        return resolve_message_identity(cls, self._identities)

    def resolve_name(self, cls: type[IMessage]) -> str:
        return self.resolve_identity(cls).name

    def resolve_version(self, cls: type[IMessage]) -> int:
        return self.resolve_identity(cls).version

    def resolve_type(self, name: str) -> type[IMessage]:
        cls = self._name_to_type.get(name)
        if cls is not None:
            return cls
        registered = sorted(self._name_to_type)
        msg = f'Unknown message type {name!r}. Registered types: {registered}'
        raise ValueError(msg)
