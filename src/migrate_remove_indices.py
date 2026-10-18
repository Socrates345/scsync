#!/usr/bin/env python3
"""
One-time migration: strip the leading 'NNN. ' index from playlist filenames
and update archive entries to match.

Run ONCE after updating scdl.cfg, before the next sync.
Safe to re-run: files already without an index prefix are left untouched.
"""

import re
import sys
from pathlib import Path

from scdl.patches.archive_paths import to_absolute, to_relative

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

INDEX_RE = re.compile(r"^(\d+)\. ")
UNSYNC_PREFIX = "[unsync] "

SC_PLAYLIST_DIR = Path(__file__).parent.parent / "playlists" / "sc"
ARCHIVE_DIR = Path(__file__).parent.parent / "archive_trackers" / "sc"


def _strip_index(name: str) -> str | None:
    """Return the name with its leading 'NNN. ' removed, or None if no index found."""
    if name.startswith(UNSYNC_PREFIX):
        rest = name[len(UNSYNC_PREFIX):]
        m = INDEX_RE.match(rest)
        if m:
            return UNSYNC_PREFIX + rest[m.end():]
        return None
    m = INDEX_RE.match(name)
    if m:
        return name[m.end():]
    return None


def migrate_files(dry_run: bool) -> int:
    renamed = 0
    for playlist_dir in sorted(SC_PLAYLIST_DIR.iterdir()):
        if not playlist_dir.is_dir():
            continue
        for f in sorted(playlist_dir.iterdir()):
            if not f.is_file():
                continue
            new_name = _strip_index(f.name)
            if new_name is None:
                continue
            new_path = f.parent / new_name
            if new_path.exists():
                print(f"  [SKIP] collision: {f.name}  →  {new_name} already exists")
                continue
            print(f"  {f.name}  ->  {new_name}")
            if not dry_run:
                f.rename(new_path)
            renamed += 1
    return renamed


def migrate_archives(dry_run: bool) -> int:
    updated_files = 0
    for arc in sorted(ARCHIVE_DIR.glob("*.txt")):
        lines = arc.read_text(encoding="utf-8", errors="replace").splitlines()
        new_lines = []
        changed = 0
        for line in lines:
            parts = line.split(" ", 2)
            if len(parts) == 3 and parts[0] == "soundcloud":
                old_path = to_absolute(parts[2])
                new_name = _strip_index(old_path.name)
                if new_name is not None:
                    new_path = old_path.parent / new_name
                    line = f"soundcloud {parts[1]} {to_relative(new_path)}"
                    changed += 1
            new_lines.append(line)
        if changed:
            print(f"  {arc.name}: {changed} path(s) updated")
            if not dry_run:
                arc.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
            updated_files += 1
    return updated_files


def main():
    dry_run = "--dry-run" in sys.argv

    if dry_run:
        print("=== DRY RUN — no files will be changed ===\n")

    print("Step 1: Renaming playlist files...")
    n_files = migrate_files(dry_run)
    print(f"  → {n_files} file(s) {'would be ' if dry_run else ''}renamed.\n")

    print("Step 2: Updating archive entries...")
    n_archives = migrate_archives(dry_run)
    print(f"  → {n_archives} archive file(s) {'would be ' if dry_run else ''}updated.\n")

    if dry_run:
        print("Re-run without --dry-run to apply changes.")
    else:
        print("Done. Run sync_sc_playlists.py as normal.")


if __name__ == "__main__":
    main()
