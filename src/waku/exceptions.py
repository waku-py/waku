__all__ = [
    'ImproperlyConfiguredError',
    'WakuError',
]


class WakuError(Exception):
    pass


class ImproperlyConfiguredError(WakuError):
    """Raised when framework configuration is invalid."""
