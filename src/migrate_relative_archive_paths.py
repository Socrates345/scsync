#!/usr/bin/env python3
"""
One-time migration: rewrite the absolute path field in archive_trackers/*.txt
sync archives to be relative to the scdl/ repo root, so the archive survives
the parent folder tree above scdl/ being moved or renamed.

Old:  soundcloud 663270089 C:\\...\\playground\\scdl\\playlists\\sc\\accordion\\Foo.m4a
New:  soundcloud 663270089 playlists\\sc\\accordion\\Foo.m4a

Locates the "playlists\\..." segment case-insensitively rather than hardcoding
any specific old prefix, since prior absolute prefixes were found to vary
(different drive-letter casing, different ancestor folders).

Usage:
    python src/migrate_relative_archive_paths.py            # dry run, prints diffs
    python src/migrate_relative_archive_paths.py --write     # apply changes
"""

import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).parent.parent  # src/ -> scdl/
ARCHIVE_DIRS = [ROOT / "archive_trackers" / d for d in ("sc", "yt", "yt-video")]

_PLAYLISTS_RE = re.compile(r"(?i)[\\/](playlists[\\/].*)$")


def migrate_file(path: Path, write: bool) -> tuple[int, int, int, int]:
    """Returns (total, rewritten, already_relative, flagged)."""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    new_lines = []
    total = rewritten = already_relative = flagged = 0

    for line in lines:
        stripped = line.strip()
        if not stripped:
            new_lines.append(line)
            continue

        total += 1
        parts = stripped.split(maxsplit=2)
        if len(parts) != 3:
            print(f"  [FLAG] {path.name}: malformed line: {stripped!r}")
            flagged += 1
            new_lines.append(line)
            continue

        ie, id_, path_field = parts

        if not Path(path_field).is_absolute():
            already_relative += 1
            new_lines.append(line)
            continue

        m = _PLAYLISTS_RE.search(path_field)
        if not m:
            print(f"  [FLAG] {path.name}: no 'playlists\\' segment found: {path_field!r}")
            flagged += 1
            new_lines.append(line)
            continue

        new_field = str(Path(m.group(1)))
        new_line = f"{ie} {id_} {new_field}"
        print(f"  {path.name}: {path_field}  ->  {new_field}")
        new_lines.append(new_line)
        rewritten += 1

    if write and rewritten:
        path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

    return total, rewritten, already_relative, flagged


def main() -> None:
    write = "--write" in sys.argv

    if not write:
        print("=== DRY RUN — no files will be changed. Pass --write to apply. ===\n")

    grand_total = grand_rewritten = grand_already = grand_flagged = 0
    files_seen = 0

    for archive_dir in ARCHIVE_DIRS:
        if not archive_dir.exists():
            continue
        for txt_path in sorted(archive_dir.glob("*.txt")):
            files_seen += 1
            total, rewritten, already_relative, flagged = migrate_file(txt_path, write)
            grand_total += total
            grand_rewritten += rewritten
            grand_already += already_relative
            grand_flagged += flagged

    print()
    print(f"Files scanned:      {files_seen}")
    print(f"Lines total:         {grand_total}")
    print(f"Lines rewritten:     {grand_rewritten}{'' if write else ' (would be)'}")
    print(f"Already relative:    {grand_already}")
    print(f"Flagged (untouched): {grand_flagged}")

    if grand_flagged:
        print("\nWARNING: some lines could not be migrated automatically — review above.")

    if not write:
        print("\nRe-run with --write to apply changes.")


if __name__ == "__main__":
    main()
