"""Local .env loading shared by the command-line tools and the server."""

from __future__ import annotations

import os
from pathlib import Path


def load_env_file(path: Path = Path(".env")) -> None:
    """Fill unset variables from a local .env; the last non-empty value wins."""
    if not path.is_file():
        return
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition("=")
        value = value.strip().strip("'\"")
        if sep and key.strip() and not key.lstrip().startswith("#") and value:
            values[key.strip()] = value
    for key, value in values.items():
        os.environ.setdefault(key, value)
