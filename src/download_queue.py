#!/usr/bin/env python3
"""Download the links queued in src/dl-audio.md and src/dl-video.md.

One link per line, added by hand at any time:
  dl-audio.md → python src/ytdl.py -l <URL>          (audio, YouTube or SoundCloud)
  dl-video.md → python src/ytdl.py --video -l <URL>  (YouTube video)

A link is deleted from its file once its download succeeded; a failed link stays
and is tried again next time. The scheduled sync (sync_sc_playlists.py
--unattended) runs this after the SoundCloud playlists. Run it by hand with:

    python src/download_queue.py
"""

from __future__ import annotations

import os
import random
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

_ROOT = Path(__file__).parent.parent
_SRC = Path(__file__).parent
YTDL_SCRIPT = _SRC / "ytdl.py"
AUDIO_FILE = _SRC / "dl-audio.md"
VIDEO_FILE = _SRC / "dl-video.md"
QUEUES = (("audio", AUDIO_FILE, False), ("video", VIDEO_FILE, True))

DELAY = 5.0  # seconds between links, ±50% jitter
FAIL_STREAK_LIMIT = 3  # this many failed links in a row → stop (offline, blocked, bad cookies)
EXIT_LOCKED = 75

# A line counts as a link only if it is a single URL, optionally as a list item
# ("- url", "1. url") or in angle brackets ("<url>"). Anything else is ignored.
_LINK_RE = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)?<?(https?://[^\s<>]+)>?\s*$")


@dataclass
class QueueResult:
    done: int = 0
    failed: list[str] = field(default_factory=list)  # links that failed (kept in the file)
    not_tried: int = 0  # links left alone after FAIL_STREAK_LIMIT failures in a row


def _link_of(line: str) -> str | None:
    m = _LINK_RE.match(line.lstrip("﻿"))
    return m.group(1) if m else None


def _read(path: Path) -> str:
    # newline="" keeps CRLF as-is, so a rewrite changes only the removed lines.
    with open(path, encoding="utf-8", newline="") as f:
        return f.read()


def read_links(path: Path) -> list[str]:
    """Links in the file, in line order, without duplicates. Missing file → []."""
    try:
        text = _read(path)
    except FileNotFoundError:
        return []
    links: list[str] = []
    for line in text.splitlines():
        url = _link_of(line)
        if url and url not in links:
            links.append(url)
    return links


def remove_link(path: Path, url: str, write=print) -> bool:
    """Delete every line holding exactly this link. The file is read again right
    now, so lines added while downloads were running are kept; every other line is
    left byte for byte. Returns False if the file couldn't be rewritten (the link
    then stays, and is found already downloaded next time)."""
    try:
        text = _read(path)
    except FileNotFoundError:
        return True
    lines = text.splitlines(keepends=True)
    kept = [ln for ln in lines if _link_of(ln) != url]
    if len(kept) == len(lines):
        return True  # already gone (removed by hand)
    new = "".join(kept)
    if text.startswith("﻿") and not new.startswith("﻿"):
        new = "﻿" + new  # the removed link was the first line: keep the BOM

    tmp = path.with_name(f".{path.name}.tmp")
    for attempt in range(3):
        try:
            with open(tmp, "w", encoding="utf-8", newline="") as f:
                f.write(new)
            os.replace(tmp, path)
            return True
        except PermissionError:  # noqa: PERF203 — Windows: file briefly locked by an editor / antivirus
            time.sleep(1 + attempt)
    tmp.unlink(missing_ok=True)
    write(f"  WARNING: couldn't update {path.name} - the link stays and is retried next run.")
    return False


def _run_ytdl(url: str, video: bool, write=print) -> bool:
    """Run ytdl.py for one link. True if it exited 0 without an ERROR: line."""
    cmd = [sys.executable, str(YTDL_SCRIPT)] + (["--video"] if video else []) + ["-l", url]
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", cwd=_ROOT,
        env={**os.environ, "PYTHONIOENCODING": "utf-8:backslashreplace"},
    )
    has_error = False
    assert proc.stdout is not None
    for line in proc.stdout:
        if line.startswith("ERROR:"):
            has_error = True
        if line.strip():
            write("  " + line.rstrip())
    proc.wait()
    return proc.returncode == 0 and not has_error


def process_list(path: Path, video: bool, write=print, delay: float = DELAY) -> QueueResult:
    """Download every link in the file, deleting each one that succeeded."""
    result = QueueResult()
    links = read_links(path)
    if not links:
        return result
    write(f"\n{path.name}: {len(links)} link(s)")
    streak = 0
    for i, url in enumerate(links, 1):
        write(f"\n[{path.name} {i}/{len(links)}] {url}")
        if _run_ytdl(url, video, write):
            remove_link(path, url, write)
            result.done += 1
            streak = 0
            write("  → downloaded, removed from the list")
        else:
            result.failed.append(url)
            streak += 1
            write("  → FAILED, kept in the list for next run")
            if streak >= FAIL_STREAK_LIMIT and i < len(links):
                result.not_tried = len(links) - i
                write(f"\n  {streak} links failed in a row - leaving the other {result.not_tried} for next run.")
                break
        if i < len(links) and delay > 0:
            time.sleep(delay * random.uniform(0.5, 1.5))
    return result


def run_queues(write=print) -> dict[str, QueueResult]:
    """Audio list first, then video list."""
    return {kind: process_list(path, video, write) for kind, path, video in QUEUES}


def summary_lines(results: dict[str, QueueResult]) -> list[str]:
    """Counts and fixed wording only, never links (also used for the ntfy alert)."""
    lines: list[str] = []
    for kind, res in results.items():
        name = f"{kind.capitalize()} list"
        if res.done:
            lines.append(f"{name}: {res.done} downloaded")
        if res.failed:
            n = len(res.failed)
            line = f"{name}: {n} link{'' if n == 1 else 's'} failed, kept for next run"
            if res.not_tried:
                line += f", {res.not_tried} not tried"
            lines.append(line + " (see logs)")
    return lines


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    from sync_sc_playlists import _acquire_lock  # noqa: PLC0415 — avoids a circular import

    lock = _acquire_lock()
    if lock is None:
        print("A sync is already running - exiting.")
        sys.exit(EXIT_LOCKED)
    try:
        results = run_queues()
    finally:
        lock.close()

    lines = summary_lines(results)
    print("\n" + ("\n".join(lines) if lines else "Nothing queued in dl-audio.md / dl-video.md."))
    sys.exit(1 if any(r.failed for r in results.values()) else 0)


if __name__ == "__main__":
    main()
