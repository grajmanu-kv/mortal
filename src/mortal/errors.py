"""Domain exceptions. Leaf module: imports nothing from the package."""


class MortalError(Exception):
    """Base class for every error this package raises on purpose."""


class ConfigError(MortalError):
    """Invalid or missing configuration."""


class LLMError(MortalError):
    """A language-model provider failed (network, HTTP status, unusable reply)."""


class NotFoundError(MortalError):
    """A life (or other resource) does not exist."""
