"""Tags that were merged into another: config/tag_aliases.yaml maps each old name to the one that replaces it."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

ALIASES_PATH = Path(__file__).resolve().parents[1] / "config" / "tag_aliases.yaml"


@lru_cache(maxsize=1)
def aliases(path: Path = ALIASES_PATH) -> dict[str, str]:
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {str(old).strip().lower(): str(new).strip().lower() for old, new in data.items() if old and new}


def merge(tags: list[str]) -> list[str]:
    """The tags under their current names, each once, in order."""
    found = aliases()
    return list(dict.fromkeys(found.get(tag.strip().lower(), tag) for tag in tags))
