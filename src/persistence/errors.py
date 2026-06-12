"""Typed errors raised by the persistence layer."""

from __future__ import annotations


class PersistenceConfigurationError(Exception):
    """Raised when DATABASE_URL is missing or uses an unsupported scheme."""


class PersistenceConnectionError(Exception):
    """Raised when the first PostgreSQL connection attempt fails."""
