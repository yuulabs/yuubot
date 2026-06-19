"""Domain errors for the resource layer."""

from __future__ import annotations


class StorageError(Exception):
    """A storage-layer error that the domain translates to a domain exception.

    Catches like ``BaseORMException`` are re-raised as ``StorageError``
    so that higher layers (HTTP handlers) can handle a consistent error
    class rather than leak storage-implementation details.
    """


class ResourceNotFoundError(StorageError):
    """A resource was requested but does not exist in storage."""
