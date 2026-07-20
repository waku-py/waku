__all__ = [
    'ImproperlyConfiguredError',
    'UnexpectedRollbackError',
    'WakuError',
]


class WakuError(Exception):
    pass


class ImproperlyConfiguredError(WakuError):
    """Raised when framework configuration is invalid."""


class UnexpectedRollbackError(WakuError):
    """Raised when a clean outer return cannot commit a rollback-only transaction."""
