"""Keep the server's vault copy to the allowed folders.

Obsidian's tool can skip folders by name but cannot say "only this folder", so a folder created later in
the vault would be copied to the server. Each cycle this removes anything outside the allowlist from the
server's copy and tells the sync tool to stop syncing those folders.

While the copy is download-only, everything outside the allowlist is deleted. In two-way sync a local delete
can reach the real vault, so then only folders that the sync tool confirms are on its skip list are deleted
(a 2026-10-18 test with the tool at 0.0.14 showed such deletes stay local); loose notes and links are kept.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import unicodedata
from pathlib import Path

from app.ingestion.vault_sync import parse_folders


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


def merged_exclusions(baseline: Path | None, extra: Path, new: list[str], keep: list[str] = ()) -> list[str]:
    """The optional baseline skip list plus every folder found since, remembered in `extra`."""
    names: list[str] = []
    for path in (baseline, extra):
        if path is not None and path.is_file():
            names += [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    # An allowed folder must never stay on the skip list, even if a mistake once put it there.
    names = [name for name in names if not any(_same(name, allowed) for allowed in keep)]
    for name in new:
        # Exact spelling, so the tool's skip list names each folder as it is on disk.
        if name not in names:
            names.append(name)
    # Only names beyond the baseline are remembered; with no baseline (the server's case), every name is.
    base = ({line.strip() for line in baseline.read_text(encoding="utf-8").splitlines()}
            if baseline is not None and baseline.is_file() else set())
    extra.write_text("\n".join(name for name in names if name not in base) + "\n", encoding="utf-8")
    return names


def tool_settings(root: Path) -> str | None:
    """The sync tool's printed settings, lower-cased, or None when they cannot be read."""
    try:
        result = subprocess.run(["ob", "sync-config", "--path", str(root)], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    return (result.stdout or "").lower() if result.returncode == 0 else None


def tool_mode(root: Path) -> str | None:
    """'pull-only' or 'bidirectional' when the tool's settings name exactly one of them; anything unclear is None."""
    text = tool_settings(root)
    if text is None:
        return None
    modes = [mode for mode in ("pull-only", "bidirectional", "mirror-remote") if mode in text]
    return modes[0] if len(modes) == 1 and modes[0] != "mirror-remote" else None


def skipped_folders(settings_dir: Path) -> list[str] | None:
    """The folder names in the sync tool's own settings file (`ignoreFolders`), or None if it cannot be read."""
    configs = list(settings_dir.glob("*/config.json")) if settings_dir.is_dir() else []
    if len(configs) != 1:
        return None
    try:
        names = json.loads(configs[0].read_text(encoding="utf-8")).get("ignoreFolders")
    except (OSError, ValueError, AttributeError):
        return None
    return [name for name in names if isinstance(name, str)] if isinstance(names, list) else None


def remove_skipped_folders(root: Path, folders: list[str], settings_dir: Path) -> list[str]:
    """Two-way mode: delete only these top-level folders, and only those the tool's settings file skips."""
    skipped = skipped_folders(settings_dir)
    if skipped is None:
        return []
    removed = []
    for name in folders:
        entry = root / name
        if (entry.is_dir() and not entry.is_symlink() and not name.startswith(".")
                # Exact spelling only: a folder is deleted only if the tool's own list names it exactly.
                and any(unicodedata.normalize("NFC", name) == unicodedata.normalize("NFC", item.strip("/"))
                        for item in skipped)):
            shutil.rmtree(entry)
            removed.append(name)
    return removed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(os.getenv("KNOWLEDGE_SOURCE_ROOT", "/data/vault")))
    parser.add_argument("--baseline", type=Path, default=None, help="optional fixed list of folder names to skip")
    parser.add_argument("--extra", type=Path, default=Path("/data/excluded-folders-extra.txt"))
    parser.add_argument("--check", action="store_true", help="only say whether this run would delete")
    parser.add_argument("--sync-settings", type=Path, default=Path.home() / ".config/obsidian-headless/sync",
                        help="the sync tool's settings folder (read for its skip list in two-way mode)")
    args = parser.parse_args(argv)
    included = list(parse_folders(os.getenv("KNOWLEDGE_INCLUDED_FOLDERS", "Knowledge")))
    if not args.root.is_dir():
        return 0
    # The setting and the sync tool's own configuration must agree on the mode before anything is deleted.
    setting = os.getenv("KNOWLEDGE_SYNC_MODE", "pull-only")
    mode = tool_mode(args.root)
    delete = setting == "pull-only" and mode == "pull-only"
    two_way = setting == "bidirectional" and mode == "bidirectional"
    if args.check:
        print("cleanup will delete outside the allowed folders" if delete else
              "cleanup will delete only skipped folders (two-way sync)" if two_way else
              "cleanup will NOT delete (sync mode not verified)")
        return 0
    outside, loose = enforce(args.root, included, delete=False)
    # If no allowed folder exists at all, the setting is probably wrong: change nothing rather than remove everything.
    if not any(entry.is_dir() and any(_same(entry.name, name) for name in included) for entry in args.root.iterdir()):
        print("none of the allowed folders exist in the vault copy; leaving everything as it is", file=sys.stderr)
        return 1
    # Tell the sync tool to skip new folders first, so a failed cleanup never leaves them syncing.
    if outside:
        names = merged_exclusions(args.baseline, args.extra, outside, included)
        try:
            result = subprocess.run(["ob", "sync-config", "--path", str(args.root), "--excluded-folders", ",".join(names)],
                                    capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.SubprocessError):
            print("could not update the skip list", file=sys.stderr)
            return 1
        if result.returncode != 0:
            print("could not update the skip list", file=sys.stderr)
            return 1
    removed: list[str] = []
    try:
        if delete:
            enforce(args.root, included, delete=True)
        elif two_way and outside:
            # The skip list was updated above; delete only what the tool now confirms it skips.
            removed = remove_skipped_folders(args.root, outside, args.sync_settings)
    except OSError as exc:
        print(f"could not remove files outside the allowed folders ({type(exc).__name__})", file=sys.stderr)
        return 1
    if outside or loose:
        if delete:
            outcome = " removed from the server copy"
        elif two_way:
            outcome = f" found; {len(removed)} skipped folders removed from the server copy (two-way sync)"
        else:
            outcome = " found (not deleted: sync mode not verified)"
        print(f"allowlist: {len(outside)} folders and {loose} loose notes outside {', '.join(included)}{outcome}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
