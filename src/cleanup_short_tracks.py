"""
Find SoundCloud GO+ preview snips already on disk: tracks whose metadata
duration looked normal (so the `duration>30` match-filter let them through)
but whose actual decoded audio is 30 seconds or shorter, because GO+ serves a
preview stream instead of the full track to non-subscribers.

For each one found in an `archive_trackers/sc/*.txt` sync archive, this
deletes the file, drops it from that archive, and appends a [FAIL] entry to
the matching `.failed` file — the same format `src/retry_failed_with_ytdl.py`
already knows how to resolve via YouTube, and the same one a fresh `--sync`
run produces going forward (see scdl/patches/snip_detection_postprocessor.py).

Usage:
    python cleanup_short_tracks.py           # dry run, lists what would change
    python cleanup_short_tracks.py --delete  # actually delete + update trackers
"""

import re
import sys
from pathlib import Path

from mutagen import File as MutagenFile

from scdl.patches.archive_paths import to_absolute

ROOT = Path(__file__).parent.parent
ARCHIVE_DIR = ROOT / "archive_trackers" / "sc"
MAX_DURATION = 30  # seconds — SoundCloud snips are exactly 30s

dry_run = "--delete" not in sys.argv

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")


def get_duration(path: Path) -> float | None:
    audio = MutagenFile(path)
    if audio is None or audio.info is None:
        return None
    return audio.info.length


def _track_name(path: Path) -> str:
    """Best-effort "uploader - title" from the filename, stripping the id/index prefix."""
    stem = re.sub(r"^\[\d+\]\s*", "", path.stem)  # "[id] uploader - title"
    return re.sub(r"^\d{2,3}\.\s*", "", stem)  # "NNN. uploader - title"


def main() -> None:
    if dry_run:
        print("Dry run — pass --delete to actually remove files and update trackers.\n")

    total = 0

    for txt_path in sorted(ARCHIVE_DIR.glob("*.txt")):
        lines = txt_path.read_text(encoding="utf-8", errors="replace").splitlines()
        keep: list[str] = []
        snips: list[tuple[str, str, float]] = []  # (track_id, name, duration)

        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                _ie, track_id, path_str = line.split(maxsplit=2)
            except ValueError:
                keep.append(line)
                continue

            path = to_absolute(path_str)
            duration = get_duration(path) if path.exists() else None
            if duration is None or duration > MAX_DURATION:
                keep.append(line)
                continue

            snips.append((track_id, _track_name(path), duration))
            status = "would delete" if dry_run else "DELETED"
            print(f"  [{status}] {duration:.1f}s  {path.name}  ({txt_path.stem})")
            if not dry_run:
                path.unlink(missing_ok=True)

        if not snips:
            continue
        total += len(snips)
        if dry_run:
            continue

        txt_path.write_text("".join(f"{ln}\n" for ln in keep), encoding="utf-8")

        with open(txt_path.with_suffix(".failed"), "a", encoding="utf-8") as fh:
            for track_id, name, duration in snips:
                fh.write(f"[FAIL]    {name}\n")
                fh.write(f"         https://api.soundcloud.com/tracks/{track_id}\n")
                fh.write(f"       → [soundcloud] {track_id}: GO+ preview snip — actual audio is {duration:.1f}s\n")
                fh.write("\n")

    n_archives = len(list(ARCHIVE_DIR.glob("*.txt")))
    print(f"\n{'Would remove' if dry_run else 'Removed'} {total} file(s) across {n_archives} archive(s).")
    if total and not dry_run:
        print("Run `python src/retry_failed_with_ytdl.py <playlist>` per affected playlist to pull them from YouTube.")


if __name__ == "__main__":
    main()
