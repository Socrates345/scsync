#!/usr/bin/env python3
"""Retry SoundCloud download failures by searching for the same track on YouTube.

Many [FAIL] entries in a playlist's .failed file (e.g. "This video is DRM
protected") are tracks that exist fine on YouTube. For each [FAIL] entry this
finds the SoundCloud track id from the logged error, searches YouTube for the
"uploader - title" pair, downloads the top match into the playlist's folder
via ytdl.py, tags the filename "[YT] " so it's identifiable in the library,
records the SoundCloud id in the playlist's sync archive (so a future
`--sync` run stops re-attempting a track that's permanently DRM-protected),
and drops the resolved entry from .failed.

Usage:
    python src/retry_failed_with_ytdl.py <playlist-name-or-failed-file> [--dry-run] [--delay S]

<playlist-name-or-failed-file> can be the .failed file's stem, a substring of it, or a path
to the .failed file directly.
"""

from __future__ import annotations

import argparse
import glob as globmod
import random
import re
import shlex
import sys
import time
from pathlib import Path

from yt_dlp.utils import locked_file, sanitize_filename

import ytdl
from scdl.patches.archive_paths import to_absolute, to_relative

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

_ROOT = Path(__file__).parent.parent
ARCHIVE_DIR = _ROOT / "archive_trackers" / "sc"

_ID_RE = re.compile(r"\[soundcloud\]\s*(\d+):")
_AUDIO_EXTS = {".m4a", ".mp3", ".opus", ".aac", ".flac", ".wav", ".ogg"}


def _resolve_failed_file(arg: str) -> Path:
    p = Path(arg)
    if p.is_file():
        return p
    candidate = ARCHIVE_DIR / (arg if arg.endswith(".failed") else f"{arg}.failed")
    if candidate.is_file():
        return candidate
    matches = sorted(ARCHIVE_DIR.glob(f"*{arg}*.failed"))
    if len(matches) == 1:
        return matches[0]
    if not matches:
        sys.exit(f"No .failed file found matching {arg!r} in {ARCHIVE_DIR}")
    sys.exit("Multiple .failed files match:\n" + "\n".join(f"  {m.name}" for m in matches))


def _parse_blocks(text: str) -> list[dict]:
    entries = []
    for block in text.split("\n\n"):
        lines = [ln for ln in block.splitlines() if ln.strip()]
        if not lines:
            continue
        m = re.match(r"\[(\w+)\]\s*(.+)", lines[0])
        if not m:
            continue
        entries.append({
            "tag": m.group(1),
            "name": m.group(2).strip(),
            "url": lines[1].strip() if len(lines) > 1 else "",
            "error": lines[2].strip() if len(lines) > 2 else "",
            "raw": block,
        })
    return entries


def _dest_folder(txt_path: Path) -> Path:
    for line in txt_path.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.strip().split(maxsplit=2)
        if len(parts) == 3:
            return to_absolute(parts[2]).parent
    sys.exit(f"{txt_path} has no entries — can't determine the playlist's download folder.")


def _search_queries(name: str) -> list[str]:
    """Primary query (uploader + title), with a title-only fallback.

    Some SoundCloud uploader handles are reposter/fan-page slugs with no
    presence on YouTube (e.g. "artemaswannabepopstar"); including them in
    the query can make ytsearch1 return zero results even though the track
    itself is easy to find by title alone.
    """
    uploader, sep, title = name.partition(" - ")
    if not sep:
        return [name.replace("-", " ")]
    combined = f"{uploader.replace('-', ' ')} {title}".strip()
    title_only = title.strip()
    return [combined, title_only] if title_only and title_only != combined else [combined]


def _attempt_download(query: str, dest_folder: Path, stub: str, cookies: str | None) -> Path | None:
    """Search YouTube and download the top match. Returns the audio file path, or
    None on failure. Any non-audio sidecar files left behind by a botched
    thumbnail embed (observed in practice) are cleaned up either way."""
    outtmpl = str(dest_folder / f"{stub}.%(ext)s")
    ns = argparse.Namespace(
        l=f"ytsearch1:{query}",
        sync=False,
        video=False,
        path=str(dest_folder),
        offset=None,
        no_playlist_folder=True,
        overwrite=False,
        cookies_from_browser=cookies,
        cookies=None,
        debug=False,
        yt_dlp_args=f"-o {shlex.quote(outtmpl)}",
    )
    try:
        ytdl.download(ns.l, ns)
    except Exception as e:
        print(f"  -> download failed: {e}")
        return None

    found = [Path(f) for f in globmod.glob(globmod.escape(str(dest_folder / stub)) + ".*")]
    audio = next((f for f in found if f.suffix.lower() in _AUDIO_EXTS), None)
    for f in found:
        if f != audio:
            f.unlink(missing_ok=True)
    return audio


def _process_failed_file(failed_path: Path, dry_run: bool, delay: float, cfg_cookies: str | None) -> tuple[int, int]:
    """Retry every [FAIL] entry in one .failed file. Returns (resolved, total)."""
    txt_path = failed_path.with_suffix(".txt")
    entries = _parse_blocks(failed_path.read_text(encoding="utf-8"))
    targets = [e for e in entries if e["tag"] == "FAIL"]

    if not targets:
        print(f"No [FAIL] entries in {failed_path.name}")
        return 0, 0

    dest_folder = _dest_folder(txt_path)
    print(f"{len(targets)} [FAIL] track(s) in {failed_path.name} -> {dest_folder}")

    resolved_ids: set[str] = set()
    resolved_entries: list[dict] = []
    still_failed: list[dict] = []

    for i, entry in enumerate(targets, 1):
        name, error = entry["name"], entry["error"]
        track_id_m = _ID_RE.search(error)
        if not track_id_m:
            print(f"[{i}/{len(targets)}] {name} — no SoundCloud id in error, skipping")
            still_failed.append(entry)
            continue
        track_id = track_id_m.group(1)
        queries = _search_queries(name)
        print(f"[{i}/{len(targets)}] {name} — searching YouTube: {queries[0]!r}")

        if dry_run:
            still_failed.append(entry)
            continue

        stub = sanitize_filename(f"[YT] {name}", restricted=False)
        found = _attempt_download(queries[0], dest_folder, stub, cfg_cookies)
        for fallback_query in queries[1:]:
            if found is not None:
                break
            print(f"  -> no match, retrying with title only: {fallback_query!r}")
            found = _attempt_download(fallback_query, dest_folder, stub, cfg_cookies)

        if found is None:
            print("  -> no file produced, leaving in .failed")
            still_failed.append(entry)
            continue

        print(f"  -> {found.name}")
        with locked_file(str(txt_path), "a", encoding="utf-8") as f:
            f.write(f"soundcloud {track_id} {to_relative(found)}\n")
        resolved_ids.add(track_id)
        resolved_entries.append(entry)

        if i < len(targets):
            time.sleep(delay * random.uniform(0.5, 1.5))

    if dry_run:
        print(f"Dry run: would retry {len(targets)} track(s).")
        return 0, len(targets)

    remaining = [e for e in entries if e not in resolved_entries]
    if remaining:
        failed_path.write_text("\n\n".join(e["raw"] for e in remaining) + "\n\n", encoding="utf-8")
    else:
        failed_path.unlink()

    print(f"Recovered {len(resolved_ids)}/{len(targets)} track(s) via YouTube.")
    if still_failed:
        print(f"{len(still_failed)} still unresolved (kept in {failed_path.name if remaining else 'n/a'}).")
    return len(resolved_ids), len(targets)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("playlist", nargs="?", help="Failed-file stem/substring or path")
    p.add_argument("--all", action="store_true",
                   help="Process every playlist with [FAIL] entries in archive_trackers/sc")
    p.add_argument("--dry-run", action="store_true", help="Show what would be retried without downloading")
    p.add_argument("--delay", type=float, default=3.0, metavar="S",
                   help="Base delay between tracks (default 3s, ±50%% jitter)")
    args = p.parse_args()

    if not args.all and not args.playlist:
        p.error("playlist is required unless --all is given")

    cfg = ytdl._load_config()
    cfg_cookies = cfg.get("ytdl", "cookies_from_browser", fallback=None) or None

    if args.all:
        failed_paths = sorted(
            f for f in ARCHIVE_DIR.glob("*.failed")
            if re.search(r"^\[FAIL\]", f.read_text(encoding="utf-8", errors="replace"), re.MULTILINE)
        )
        if not failed_paths:
            print("No playlists with [FAIL] entries.")
            return

        total_resolved = total_targets = 0
        for i, failed_path in enumerate(failed_paths, 1):
            print(f"\n--- [{i}/{len(failed_paths)}] {failed_path.stem} ---")
            resolved, total = _process_failed_file(failed_path, args.dry_run, args.delay, cfg_cookies)
            total_resolved += resolved
            total_targets += total

        print(f"\n{total_resolved}/{total_targets} recovered via YouTube across {len(failed_paths)} playlist(s).")
        return

    failed_path = _resolve_failed_file(args.playlist)
    _process_failed_file(failed_path, args.dry_run, args.delay, cfg_cookies)


if __name__ == "__main__":
    main()
