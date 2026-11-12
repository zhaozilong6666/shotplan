"""Typed errors so callers can distinguish recoverable failures."""

from __future__ import annotations


class ShotplanError(Exception):
    """Base class for all shotplan errors."""


class ConfigError(ShotplanError):
    """Invalid or missing configuration."""


class KnowledgeError(ShotplanError):
    """Knowledge base missing, malformed or inconsistent."""


class ProviderError(ShotplanError):
    """Model provider failed (network, auth, timeout, rate limit)."""


class ParseError(ShotplanError):
    """Model output could not be turned into a valid Plan."""


class StorageError(ShotplanError):
    """Persistence layer failed."""
