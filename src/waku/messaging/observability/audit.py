import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Annotated, get_args, get_origin, get_type_hints

from waku.exceptions import ImproperlyConfiguredError
from waku.messaging.contracts.message import IMessage

__all__ = ['Audit', 'AuditedMemberResolver']

logger = logging.getLogger(__name__)

_AuditedField = tuple[str, str]  # (attr_name, structured-field key)


@dataclass(frozen=True, slots=True)
class Audit:
    """Marks a message field for structured logging.

    WARNING: the value WILL be logged in plaintext — never annotate secrets/PII. `heading` renames the
    structured-field key. Must be a runtime import (not under TYPE_CHECKING) so the annotation resolves under
    ``from __future__ import annotations``.
    """

    heading: str | None = None


class AuditedMemberResolver:
    __slots__ = ('_cache', '_overrides')

    def __init__(self, overrides: Mapping[type[IMessage], Sequence[str]]) -> None:
        self._overrides = overrides
        self._cache: dict[type[IMessage], tuple[_AuditedField, ...]] = {}

    def resolve(self, message_type: type[IMessage]) -> tuple[_AuditedField, ...]:
        cached = self._cache.get(message_type)
        if cached is not None:
            return cached
        annotated, hints = self._from_annotations(message_type)
        override = self._overrides.get(message_type)
        if override is not None:
            if hints is not None:
                unknown = [name for name in override if name not in hints]
                if unknown:
                    msg = f'audited_members for {message_type.__name__} names unknown field(s): {unknown}'
                    raise ImproperlyConfiguredError(msg)
            seen = {name for name, _ in annotated}
            annotated = (*annotated, *((name, name) for name in override if name not in seen))
        self._cache[message_type] = annotated
        return annotated

    def extract(self, payload: IMessage) -> dict[str, object]:
        result: dict[str, object] = {}
        for attr_name, key in self.resolve(type(payload)):
            try:
                result[key] = getattr(payload, attr_name)
            except AttributeError:
                logger.warning('Audited member %r missing on %s; skipping', attr_name, type(payload).__name__)
        return result

    @staticmethod
    def _from_annotations(
        message_type: type[IMessage],
    ) -> tuple[tuple[_AuditedField, ...], Mapping[str, object] | None]:
        try:
            hints = get_type_hints(message_type, include_extras=True)
        except Exception as exc:  # noqa: BLE001 -- introspection failure degrades logging, never crashes the type
            logger.warning('Cannot introspect audited members for %s: %s', message_type.__name__, exc)
            return (), None
        fields: list[_AuditedField] = []
        for name, hint in hints.items():
            if get_origin(hint) is not Annotated:
                continue
            for meta in get_args(hint)[1:]:
                if isinstance(meta, Audit):
                    fields.append((name, meta.heading or name))
                    break
        return tuple(fields), hints
