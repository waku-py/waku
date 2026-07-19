from __future__ import annotations

from waku.exceptions import ImproperlyConfiguredError

__all__ = ['ValidationError']


class ValidationError(ImproperlyConfiguredError):
    """Module-graph validation failure — a non-recoverable misconfiguration to fix at setup.

    A config error, so it lives under ``ImproperlyConfiguredError`` (the non-recoverable
    misconfiguration base), not a bare ``WakuError`` sibling.
    """
