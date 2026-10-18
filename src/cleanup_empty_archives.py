"""
Delete empty (0-byte) files in archive_trackers/sc.

scdl rewrites a playlist's .failed file on every run, so a playlist with no
failures leaves an empty .failed behind; an empty .txt archive is treated the
same as a missing one by both `scdl --sync` and sync_sc_playlists.py. Nothing
is lost by removing them.

Runs automatically at the end of sync_sc_playlists.py, or on its own:
    python src/cleanup_empty_archives.py
"""

from pathlib import Path

ARCHIVE_DIR = Path(__file__).parent.parent / "archive_trackers" / "sc"


def delete_empty(archive_dir: Path = ARCHIVE_DIR) -> list[Path]:
    """Delete every 0-byte file directly in archive_dir. Returns the deleted paths."""
    if not archive_dir.is_dir():
        return []
    removed = []
    for p in sorted(archive_dir.iterdir()):
        if p.is_file() and p.stat().st_size == 0:
            p.unlink()
            removed.append(p)
    return removed


def main() -> None:
    removed = delete_empty()
    print(f"Removed {len(removed)} empty file(s) from archive_trackers/sc.")


if __name__ == "__main__":
    main()
