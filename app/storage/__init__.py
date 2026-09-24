"""Private Postgres storage and candidate search for captured runs."""


class StorageError(ValueError):
    """A run, publication selection, or search request is invalid."""
