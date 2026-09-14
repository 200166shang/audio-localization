"""Stable error taxonomy for user-facing recovery."""


class LocalizationError(RuntimeError):
    """Base class for expected localization failures."""


class ConfigurationError(LocalizationError):
    """Credentials, service activation, or local dependency is missing."""


class PermanentCloudError(LocalizationError):
    """A request cannot succeed without changing configuration or input."""


class TransientCloudError(LocalizationError):
    """A request may succeed when retried."""


class TimelineError(LocalizationError):
    """Synthesized speech cannot fit the requested timeline safely."""
