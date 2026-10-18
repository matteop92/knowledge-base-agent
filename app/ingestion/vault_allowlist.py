"""Keep the server's vault copy to the allowed folders.

Obsidian's tool can skip folders by name but cannot say "only this folder", so a folder created later in
the vault would be copied to the server. Each cycle this removes anything outside the allowlist from the
server's copy and tells the sync tool to stop syncing those folders.

Deleting is safe only while the copy is download-only: in two-way sync a local delete would reach the real
vault, so then nothing is deleted and folders are only added to the skip list.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import unicodedata
from pathlib import Path


def _same(a: str, b: str) -> bool:
    return unicodedata.normalize("NFC", a).casefold() == unicodedata.normalize("NFC", b).casefold()


def enforce(root: Path, included: list[str], *, delete: bool) -> tuple[list[str], int]:
    """Return the folders outside the allowlist and the number of loose files removed.

    Entries that start with a dot (`.obsidian`, `.trash`) are left alone.
    """
    if not included:
        raise ValueError("the allowlist must name at least one folder")
    outside, loose = [], 0
    for entry in sorted(root.iterdir(), key=lambda item: item.name):
        if entry.name.startswith("."):
            continue
        if entry.is_symlink():
            if delete:
                entry.unlink()
            continue
        if entry.is_dir():
            if not any(_same(entry.name, name) for name in included):
                outside.append(entry.name)
                if delete:
                    shutil.rmtree(entry)
        elif entry.is_file():
            loose += 1
            if delete:
                entry.unlink()
    return outside, loose


def merged_exclusions(baseline: Path, extra: Path, new: list[str]) -> list[str]:
    """The baseline skip list plus every folder found since, remembered in `extra`."""
    names: list[str] = []
    for path in (baseline, extra):
        if path.is_file():
            names += [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    for name in new:
        if not any(_same(name, existing) for existing in names):
            names.append(name)
    extra.write_text("\n".join(name for name in names if not any(
        _same(name, line.strip()) for line in (baseline.read_text(encoding="utf-8").splitlines()
                                                if baseline.is_file() else []))) + "\n", encoding="utf-8")
    return names


def tool_is_pull_only(root: Path) -> bool:
    """Ask the sync tool itself; anything unclear counts as not download-only."""
    try:
        result = subprocess.run(["ob", "sync-config", "--path", str(root)], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return False
    text = (result.stdout or "").lower()
    return result.returncode == 0 and "pull-only" in text and "bidirectional" not in text and "mirror-remote" not in text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(os.getenv("KNOWLEDGE_SOURCE_ROOT", "/data/vault")))
    parser.add_argument("--baseline", type=Path, default=Path("deploy/excluded-folders.txt"))
    parser.add_argument("--extra", type=Path, default=Path("/data/excluded-folders-extra.txt"))
    args = parser.parse_args(argv)
    included = [name.strip() for name in os.getenv("KNOWLEDGE_INCLUDED_FOLDERS", "Knowledge").split(",") if name.strip()]
    if not args.root.is_dir():
        return 0
    # Both the setting and the sync tool's own configuration must say download-only before anything is deleted.
    delete = os.getenv("KNOWLEDGE_SYNC_MODE", "pull-only") == "pull-only" and tool_is_pull_only(args.root)
    outside, loose = enforce(args.root, included, delete=delete)
    if outside:
        names = merged_exclusions(args.baseline, args.extra, outside)
        result = subprocess.run(["ob", "sync-config", "--path", str(args.root), "--excluded-folders", ",".join(names)],
                                capture_output=True, text=True)
        if result.returncode != 0:
            print("could not update the skip list", file=sys.stderr)
            return 1
    if outside or loose:
        print(f"allowlist: {len(outside)} folders and {loose} loose notes outside {', '.join(included)}"
              f"{' removed from the server copy' if delete else ' found (not deleted: two-way sync)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
