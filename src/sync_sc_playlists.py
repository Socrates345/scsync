#!/usr/bin/env python3
import argparse
import contextlib
import ctypes
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
import traceback
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from tqdm import tqdm

from cleanup_empty_archives import delete_empty
from ytdlp_age import ytdlp_update_hint

_ROOT = Path(__file__).parent.parent
PLAYLIST_FILE = Path(__file__).parent / "sc-playlists-list.md"
ARCHIVE_DIR = _ROOT / "archive_trackers" / "sc"
SC_PLAYLIST_DIR = _ROOT / "playlists" / "sc"
STATE_FILE = _ROOT / ".sync-state.json"
LOCK_FILE = _ROOT / ".sync.lock"
LAST_RUN_FILE = _ROOT / ".sync-last.json"  # failing playlists from the last --unattended run
ENV_FILE = _ROOT / ".env"
LOG_DIR = _ROOT / "logs"
LOG_KEEP = 30
EXIT_LOCKED = 75  # another sync holds the lock
RATE_LIMIT_COOLDOWN = 120
_SANITY_N = 3   # first N tracks fetched to verify playlist identity
_SCAN_FAIL_LIMIT = 3  # unattended: this many playlists in a row that can't be checked → stop

_verbose = False


def parse_args():
    p = argparse.ArgumentParser(description="Sync all SoundCloud playlists in sc-playlists-list.md")
    p.add_argument("--delay", type=float, default=8.0, metavar="S",
        help="Base inter-playlist delay in seconds (default 8, actual ±50%% jitter)")
    p.add_argument("--max-errors", type=int, default=5, metavar="N",
        help="Abort a playlist after N consecutive track errors (default 5)")
    p.add_argument("--force-all", action="store_true",
        help="Disable fast-path: full sync every playlist (also detects removed tracks)")
    p.add_argument("--resume", action="store_true",
        help="Resume the last interrupted run from where it left off")
    p.add_argument("--from", dest="from_index", type=int, metavar="N",
        help="Skip playlists 1..(N-1) and start at index N (1-based)")
    p.add_argument("--only", type=int, nargs="+", metavar="N",
        help="Run only these playlist numbers (1-based, space-separated)")
    p.add_argument("--skip", type=int, nargs="+", metavar="N",
        help="Skip these playlist numbers (1-based, space-separated)")
    p.add_argument("--verbose", action="store_true",
        help="Show all scdl/yt-dlp output without filtering")
    p.add_argument("--unattended", action="store_true",
        help="Scheduled-run mode: log to logs/, no prompts, ntfy alert, never full-sync an existing playlist")
    p.add_argument("--notify-test", action="store_true",
        help="Send a test alert to SC_NOTIFY_URL and exit")
    return p.parse_args()


# ---------------------------------------------------------------------------
# Archive / URL helpers
# ---------------------------------------------------------------------------

def archive_path(url: str) -> Path:
    url_path = urlparse(url).path.strip("/")
    parts = [p for p in url_path.split("/") if p != "sets"]
    return ARCHIVE_DIR / f"{'_'.join(parts) if parts else 'unknown'}.txt"


def is_empty(path: Path) -> bool:
    return not path.exists() or path.stat().st_size == 0


def _label(url: str) -> str:
    path = urlparse(url).path.strip("/")
    return path.replace("/sets/", " / ").replace("/", " / ")


def _list_urls() -> list[str]:
    """Playlist URLs in sc-playlists-list.md, in line order."""
    return [
        line.strip()
        for line in PLAYLIST_FILE.read_text(encoding="utf-8").splitlines()
        if line.strip().startswith("http")
    ]


_TOKEN_SUFFIX_RE = re.compile(r"^(.+?)(?:_s-[A-Za-z0-9]+)?$")


def _carry_over_archive(url: str, write) -> None:
    """
    When a playlist's secret link changes (new s-<token> in the URL, or a public
    playlist made private), its archive name changes too. Rename the archive left
    behind by the old link to the new name so the sync picks up where it was.
    Only an archive with the same user + slug that no other listed URL uses counts.
    """
    new = archive_path(url)
    if not is_empty(new):
        return
    prefix = _TOKEN_SUFFIX_RE.match(new.stem).group(1)
    claimed = {archive_path(u).stem for u in _list_urls()}
    candidates = [
        p for p in ARCHIVE_DIR.glob("*.txt")
        if p.stem not in claimed
        and _TOKEN_SUFFIX_RE.match(p.stem).group(1) == prefix
        and not is_empty(p)
    ]
    if not candidates:
        return
    if len(candidates) > 1:
        names = ", ".join(p.name for p in candidates)
        write(f"  [carry-over] several old archives match ({names}) — skipped, full sync instead.")
        return
    old = candidates[0]
    for suffix in (".txt", ".failed", ".premium"):
        src = old.with_suffix(suffix)
        if src.exists():
            src.replace(new.with_suffix(suffix))
    write(f"  [carry-over] {old.name} → {new.name}")


# ---------------------------------------------------------------------------
# Resume state
# ---------------------------------------------------------------------------

def _save_state(urls: list[str], completed: int) -> None:
    STATE_FILE.write_text(json.dumps({"urls": urls, "completed": completed}), encoding="utf-8")


def _load_state() -> dict | None:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8")) if STATE_FILE.exists() else None
    except Exception:
        return None


def _clear_state() -> None:
    STATE_FILE.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Single-run lock, keep-awake
# ---------------------------------------------------------------------------

def _acquire_lock(path: Path | None = None):
    """
    Exclusive OS-level lock so a manual run and a scheduled run never write the
    same archives at once. Returns the open handle (keep it for the life of the
    process; the OS releases the lock on exit, even after a crash), or None if
    another run holds it.
    """
    fh = open(path or LOCK_FILE, "a+b")  # noqa: SIM115 — must stay open while the run lasts
    try:
        if sys.platform == "win32":
            import msvcrt  # noqa: PLC0415 — Windows-only module
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl  # noqa: PLC0415 — POSIX-only module
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    return fh


def _keep_awake() -> None:
    """Windows: stop idle sleep from cutting a run short. Cleared when the process exits."""
    if sys.platform != "win32":
        return
    es_continuous, es_system_required = 0x80000000, 0x00000001
    with contextlib.suppress(Exception):
        ctypes.windll.kernel32.SetThreadExecutionState(es_continuous | es_system_required)


# ---------------------------------------------------------------------------
# Smart scan (fast-path)
# ---------------------------------------------------------------------------

def _archive_ids(url: str) -> set[str]:
    p = archive_path(url)
    if not p.exists():
        return set()
    ids: set[str] = set()
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.strip().split()
        if len(parts) >= 2:
            ids.add(parts[1])
    return ids


def _fetch_ids_at(url: str, items: str) -> list[str] | str | None:
    """
    Fetch track IDs for the given playlist-items range via yt-dlp Python API.
    Returns a list (possibly empty if range is past end), _GONE if the playlist
    URL itself returns 404, or None on any other error.

    Uses the API directly instead of a subprocess to avoid Windows console
    output bypass (WriteConsoleW leaks through capture_output=True on Windows).
    """
    try:
        from yt_dlp import YoutubeDL
        ids: list[str] = []
        params = {
            "extract_flat": True,
            "playlist_items": items,
            "quiet": True,
            "no_warnings": True,
            "allowed_extractors": ["soundcloud.*"],
        }
        with YoutubeDL(params) as ydl:
            info = ydl.extract_info(url, download=False)
        if info:
            for entry in info.get("entries") or []:
                if entry and entry.get("id"):
                    ids.append(str(entry["id"]))
        return ids
    except Exception as e:
        return _GONE if "HTTP Error 404" in str(e) else None


_FULL_SYNC = "full"
_SKIP = "skip"
_STALE = "stale"
_GONE = "gone"
_MISMATCH = "mismatch"
_SCAN_FAILED = "scan-failed"


def _fast_path(url: str, force_all: bool = False) -> str | int:
    """
    Determine the sync strategy for this playlist.

    Returns:
      _STALE        — link is dead (404 or 0 tracks), e.g. secret link reset or playlist
                      made private; don't run scdl, the user needs a fresh share link
      _FULL_SYNC    — run a full scdl --sync (new playlist or --force-all)
      _MISMATCH     — none of the first tracks are in the archive (reshuffled / replaced)
      _SCAN_FAILED  — a pre-scan request errored
      _SKIP         — nothing new, skip entirely
      int N         — run scdl with -o N (start downloading from position N)

    run_scdl() turns _MISMATCH and _SCAN_FAILED into a full sync on manual runs;
    --unattended runs flag them instead.

    Algorithm (assumes new tracks are appended to the END of the playlist):
      1. Fetch the first _SANITY_N SC track IDs (the oldest songs in the playlist).
         404 or nothing at all → stale link. Done even for new playlists and --force-all.
      2. N = number of tracks in the local archive (= songs we already have).
         The fetched IDs should be in the archive. If none match, the playlist may have
         been replaced or drastically reshuffled → mismatch.
      3. Check whether position N+1 exists on SC.
         No  → playlist unchanged → skip.
         Yes → new songs start at N+1 → run scdl with -o N+1.
    """
    heads = _fetch_ids_at(url, f"1:{_SANITY_N}")
    if heads == _GONE or heads == []:
        return _STALE
    if not isinstance(heads, list):
        return _SCAN_FAILED

    known = _archive_ids(url)
    N = len(known)
    if force_all or N == 0:
        return _FULL_SYNC  # forced, or new playlist

    # Sanity check: oldest songs (positions 1.._SANITY_N) should be in archive
    if not any(tid in known for tid in heads):
        return _MISMATCH

    # Check if a new song exists at position N+1
    next_ids = _fetch_ids_at(url, f"{N + 1}:{N + 1}")
    if not isinstance(next_ids, list):
        return _SCAN_FAILED
    if not next_ids:
        return _SKIP  # nothing at N+1 → playlist unchanged

    return N + 1  # new songs start here


# ---------------------------------------------------------------------------
# Output filtering
# ---------------------------------------------------------------------------

_STALE_MARKER = "Stale playlist URL"  # logged by SyncDownloadHelper.post_download
# Logged by SnipDetectionPP right after a "[download] Destination:" line, when the file
# turned out to be a 30s GO+ preview and was deleted. Not a new track: it's left out of
# the archive (and listed in .failed as [FAIL] for the YouTube retry), so it comes back
# on every run until recovered.
_SNIP_START = "[SnipDetection] Discarding"

_SHOW_STARTS = ("ERROR:", "WARNING:")
_SHOW_IN = ("[download] Destination:", "Playlist renamed", _STALE_MARKER)
_HIDE_STARTS = (
    "[soundcloud]", "[info]", "[debug]", "[MutagenPP]", "[Merger]",
    "[ExtractAudio]", "[ThumbnailsConvertor]", "[ThumbnailPP]",
    "[FixupM4a]", "[Mutagen]", "[FixupM3u8]", "[EmbedThumbnail]", "[FFmpegMetadata]",
)
_HIDE_IN = (
    "SCDL version", "Generating dynamic client_id", "Sync archive:",
    "Invalid client_id", "dynamically generated", "[download] 100%",
    "Using a dynamically generated",
    "has already been recorded in the archive", "has already been downloaded",
)

# ERROR: lines that are expected noise (deleted/unavailable tracks) — suppressed unless --verbose
_SUPPRESS_ERRORS = (
    "Unable to download JSON metadata: HTTP Error 404",
)


def _show(line: str) -> bool:
    if _verbose:
        return bool(line.strip())
    s = line.strip()
    if not s:
        return False
    if any(s.startswith(p) for p in _SHOW_STARTS):
        if any(sub in s for sub in _SUPPRESS_ERRORS):
            return False
        return True
    if any(sub in s for sub in _SHOW_IN):
        return True
    if any(s.startswith(p) for p in _HIDE_STARTS):
        return False
    if any(sub in s for sub in _HIDE_IN):
        return False
    return True


# ---------------------------------------------------------------------------
# scdl runner
# ---------------------------------------------------------------------------

_BASE_YT_DLP_ARGS = " ".join([
    "--sleep-requests 1",
    "--extractor-retries 5",
    "--retry-sleep extractor:60",
])

_CONN_ERRORS = [
    "ConnectionError:", "curl: (7)", "ClientIDGenerationError:", "No asset scripts found",
]


def _scdl_exe() -> str:
    """scdl next to the running interpreter (the venv), so the venv doesn't need activating."""
    return shutil.which("scdl", path=str(Path(sys.executable).parent)) or "scdl"


def _no_run(**flags) -> dict:
    """stats for a playlist where scdl was not started."""
    return {"downloaded": 0, "snips": 0, "skipped": True, "stale": False,
            "needs_full": False, "scan_failed": False, "title": None, "finished": False, **flags}


_PLAYLIST_TITLE_RE = re.compile(r"^\[download\] Downloading playlist: (.+)$")
_PLAYLIST_DONE = "[download] Finished downloading playlist"


def run_scdl(
    url: str,
    max_errors: int,
    force_all: bool = False,
    write=None,
    unattended: bool = False,
) -> tuple[bool, bool, bool, list[str], dict]:
    """
    Returns (success, aborted, is_conn_error, error_lines, stats).
    stats: {"downloaded": int, "snips": int, "skipped": bool, "stale": bool,
            "needs_full": bool, "scan_failed": bool, "title": str | None, "finished": bool}
      downloaded: new tracks kept. snips: GO+ previews downloaded then discarded
        (not counted in downloaded; they're tracks that couldn't be downloaded).
      skipped=True means scdl was not run (nothing new, or one of the flags below).
      stale=True means the link is dead (404 / 0 tracks) and needs replacing.
      needs_full=True (unattended only): the quick check didn't recognise the playlist;
        a full sync could mark tracks [unsync], so it's left for a manual run.
      scan_failed=True (unattended only): the quick check errored; retried next run.
      title: the playlist's SoundCloud title, as scdl printed it (None if scdl didn't run).
      finished=True means scdl went through the whole playlist, so any errors were
        individual tracks (geo-blocked, removed, ...) rather than the playlist itself.

    write: callable for printing filtered output lines (default: print).
           Pass tqdm.write when inside a tqdm loop so lines appear above the bar.
    """
    if write is None:
        write = print

    _carry_over_archive(url, write)
    strategy = _fast_path(url, force_all)

    if strategy == _STALE:
        return False, False, False, [], _no_run(stale=True)
    if strategy == _SKIP:
        return True, False, False, [], _no_run()
    if strategy in (_MISMATCH, _SCAN_FAILED):
        if unattended and not force_all:
            if strategy == _MISMATCH:
                return True, False, False, [], _no_run(needs_full=True)
            return False, False, False, [], _no_run(scan_failed=True)
        strategy = _FULL_SYNC

    cmd = [
        _scdl_exe(), "-l", url, "--sync", "--path", str(SC_PLAYLIST_DIR),
        "--hide-progress", "--yt-dlp-args", _BASE_YT_DLP_ARGS,
    ]
    if isinstance(strategy, int):
        # Start from position N+1. SyncDownloadHelper sees -o is set and marks
        # all archive entries not downloaded this run as "not evaluated", so they
        # are preserved rather than falsely marked [unsync].
        cmd += ["-o", str(strategy)]

    # cwd matters: scdl puts its archives under <cwd>/archive_trackers/sc.
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", cwd=_ROOT,
    )

    has_error = False
    consecutive_errors = 0
    aborted = False
    is_conn_error = False
    error_lines: list[str] = []
    downloaded = 0
    snips = 0
    stale = False
    title: str | None = None
    finished = False

    for line in proc.stdout:
        if any(m in line for m in _CONN_ERRORS):
            is_conn_error = True

        if _STALE_MARKER in line:
            stale = True

        m = _PLAYLIST_TITLE_RE.match(line.rstrip())
        if m and title is None:
            title = m.group(1).strip()
        if line.startswith(_PLAYLIST_DONE):
            finished = True

        if line.startswith("ERROR:"):
            has_error = True
            consecutive_errors += 1
            error_lines.append(line.rstrip())
            if consecutive_errors >= max_errors:
                write(f"  [abort] {consecutive_errors} consecutive errors — rate limited.")
                proc.terminate()
                try:
                    proc.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                aborted = True
                break
        elif "[soundcloud]" in line or "[download] Destination:" in line:
            # Reset on any successfully-processed track, not just active downloads.
            # Prevents scattered errors across a large already-synced playlist from
            # triggering the abort as if they were truly consecutive.
            consecutive_errors = 0

        if "[download] Destination:" in line:
            downloaded += 1
        if line.startswith(_SNIP_START):
            snips += 1

        if _show(line):
            write(line.rstrip())

    proc.wait()
    success = proc.returncode == 0 and not has_error
    return success, aborted, is_conn_error, error_lines, {
        "downloaded": max(downloaded - snips, 0), "snips": snips, "skipped": False, "stale": stale,
        "needs_full": False, "scan_failed": False, "title": title,
        "finished": finished and not aborted,
    }


# ---------------------------------------------------------------------------
# Sync loop
# ---------------------------------------------------------------------------

@dataclass
class RunResult:
    downloaded: int = 0
    failed: list[str] = field(default_factory=list)  # playlists that didn't sync
    stale: list[str] = field(default_factory=list)
    needs_full: list[str] = field(default_factory=list)
    new_tracks: dict[str, int] = field(default_factory=dict)    # url → tracks downloaded
    # url → tracks that failed in a playlist that otherwise synced (geo-blocked, removed, ...)
    track_errors: dict[str, int] = field(default_factory=dict)
    titles: dict[str, str] = field(default_factory=dict)        # url → SoundCloud playlist title


def sync_list(
    urls: list[str],
    delay: float,
    max_errors: int,
    force_all: bool,
    track_state: bool = False,
    label: str = "",
    _state_urls: list[str] | None = None,
    _state_offset: int = 0,
    unattended: bool = False,
) -> RunResult:
    """Sync URLs in order."""
    result = RunResult()
    total = len(urls)
    scan_fail_streak = 0

    with tqdm(total=total, desc=f"{label}playlists", unit="pl", dynamic_ncols=True,
              disable=unattended) as pbar:
        for i, url in enumerate(urls, 1):
            lbl = _label(url)
            pbar.set_description(f"{label}{lbl[:50]}")
            tqdm.write(f"\n[{i}/{total}] {lbl}")

            t0 = time.time()
            ok, aborted, is_conn_error, error_lines, stats = run_scdl(
                url, max_errors, force_all, write=tqdm.write, unattended=unattended,
            )
            elapsed = time.time() - t0

            if stats["stale"] and stats["skipped"]:
                tqdm.write("  → STALE LINK (404 / 0 tracks) — replace it in sc-playlists-list.md")
            elif stats["needs_full"]:
                tqdm.write("  → NEEDS MANUAL FULL SYNC (first tracks not in the archive) — skipped")
            elif stats["scan_failed"]:
                tqdm.write("  → couldn't check the playlist — retried next run")
            elif stats["skipped"]:
                tqdm.write("  → up to date")
            else:
                parts: list[str] = []
                if stats["downloaded"]:
                    parts.append(f"{stats['downloaded']} downloaded")
                if stats["snips"]:
                    parts.append(f"{stats['snips']} GO+ preview(s) discarded")
                if error_lines:
                    parts.append(f"{len(error_lines)} error(s)")
                if stats["stale"]:
                    parts.append("STALE LINK?")
                tqdm.write(f"  → {', '.join(parts) or 'nothing new'} ({elapsed:.0f}s)")

            result.downloaded += stats["downloaded"]
            if stats["downloaded"]:
                result.new_tracks[url] = stats["downloaded"]
            if stats["title"]:
                result.titles[url] = stats["title"]
            if stats["stale"]:
                result.stale.append(url)
            elif stats["needs_full"]:
                result.needs_full.append(url)
            elif not ok and not (stats["finished"] and error_lines and not is_conn_error):
                result.failed.append(url)
            elif error_lines or stats["snips"]:
                # The playlist synced; only some tracks didn't (geo-blocked, GO+ only, ...).
                result.track_errors[url] = len(error_lines) + stats["snips"]

            scan_fail_streak = scan_fail_streak + 1 if stats["scan_failed"] else 0
            stop = is_conn_error or scan_fail_streak >= _SCAN_FAIL_LIMIT

            pbar.update(1)

            if track_state and not stop:
                _save_state(_state_urls if _state_urls is not None else urls, _state_offset + i)

            if stop:
                rest = urls[i:]
                if rest:
                    why = "IP block" if is_conn_error else f"{scan_fail_streak} playlists in a row couldn't be checked"
                    tqdm.write(f"\n  [sync] {why} — skipping {len(rest)} remaining.")
                    result.failed.extend(rest)
                break

            if i < total:
                if aborted:
                    tqdm.write(f"\n  [sync] Rate limit — cooling down {RATE_LIMIT_COOLDOWN}s...")
                    time.sleep(RATE_LIMIT_COOLDOWN)
                elif delay > 0:
                    wait = delay * random.uniform(0.5, 1.5)
                    tqdm.write(f"  Waiting {wait:.1f}s... ({total - i} remaining)")
                    time.sleep(wait)

    return result


# ---------------------------------------------------------------------------
# Unattended runs: log file, ntfy alert
# ---------------------------------------------------------------------------

def _start_log() -> Path:
    """Send all output to a new file in logs/ and keep only the newest LOG_KEEP."""
    LOG_DIR.mkdir(exist_ok=True)
    path = LOG_DIR / f"sc-sync_{datetime.now().astimezone():%Y-%m-%d_%H%M%S}.log"
    fh = open(path, "a", encoding="utf-8", buffering=1)  # noqa: SIM115 — lives until exit
    sys.stdout = sys.stderr = fh
    for old in sorted(LOG_DIR.glob("sc-sync_*.log"))[:-LOG_KEEP]:
        old.unlink(missing_ok=True)
    return path


def _log_header() -> bool:
    """Print what the scheduled run resolved. Returns True if ffmpeg is missing."""
    scdl = _scdl_exe()
    ffmpeg = shutil.which("ffmpeg")
    if not Path(scdl).is_absolute():
        scdl = f"not next to python; PATH gives {shutil.which('scdl')}"
    print(f"Scheduled sync started {datetime.now().astimezone():%Y-%m-%d %H:%M:%S}")
    print(f"  python: {sys.executable}")
    print(f"  scdl:   {scdl}")
    print(f"  ffmpeg: {ffmpeg or 'NOT FOUND on PATH'}\n")
    return ffmpeg is None


def _read_env(path: Path | None = None) -> dict[str, str]:
    """KEY=VALUE lines from .env (comments, blank lines and surrounding quotes ignored)."""
    env: dict[str, str] = {}
    try:
        text = (path or ENV_FILE).read_text(encoding="utf-8-sig")
    except OSError:
        return env
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        env[key] = value
    return env


def _notify(title: str, body: str, tags: str = "") -> bool:
    """POST an alert to the ntfy topic in SC_NOTIFY_URL. Never raises; the URL is never printed."""
    url = os.environ.get("SC_NOTIFY_URL") or _read_env().get("SC_NOTIFY_URL")
    if not url:
        print("[notify] SC_NOTIFY_URL not set (.env) — no alert sent.")
        return False
    headers = {"Title": title, "User-Agent": "sc-sync"}
    if tags:
        headers["Tags"] = tags
    req = urllib.request.Request(url, data=body.encode("utf-8"), headers=headers, method="POST")
    err = _post(req)
    if err:
        time.sleep(3)
        err = _post(req)
    print(f"[notify] alert failed ({err})." if err else "[notify] alert sent.")
    return not err


def _post(req: urllib.request.Request) -> str:
    """Send the request. Returns '' on success, else the error's type name."""
    try:
        with urllib.request.urlopen(req, timeout=15):
            pass
    except Exception as e:
        return type(e).__name__
    return ""


def _fmt_ranges(nums: list[int]) -> str:
    """[1, 2, 3, 7] → '1-3, 7'"""
    nums = sorted(set(nums))
    parts: list[str] = []
    i = 0
    while i < len(nums):
        j = i
        while j + 1 < len(nums) and nums[j + 1] == nums[j] + 1:
            j += 1
        parts.append(str(nums[i]) if i == j else f"{nums[i]}-{nums[j]}")
        i = j + 1
    return ", ".join(parts)


def _lines_label(urls: list[str], line_of: dict[str, int]) -> str:
    """'line 4' / 'lines 3-5, 9' for these URLs — line numbers only, never the URL itself."""
    nums = sorted({line_of[u] for u in urls if u in line_of})
    unknown = len({u for u in urls if u not in line_of})
    label = ""
    if nums:
        single = len(nums) == 1
        label = ("line " if single else "lines ") + _fmt_ranges(nums)
    if unknown:
        label = (label + " + " if label else "") + f"{unknown} no longer in the list"
    return label


def _s(n: int) -> str:
    return "" if n == 1 else "s"


def _playlist_name(url: str, result: RunResult, line_of: dict[str, int]) -> str:
    """The playlist's SoundCloud title, else its line number. Never the URL or slug."""
    if result.titles.get(url):
        return result.titles[url]
    return f"line {line_of[url]}" if url in line_of else "a playlist no longer in the list"


def _per_playlist(counts: dict[str, int], result: RunResult, line_of: dict[str, int]) -> list[str]:
    """'- Metal: 4' per playlist, in list order."""
    order = sorted(counts, key=lambda u: line_of.get(u, 1_000_000))
    return [f"- {_playlist_name(u, result, line_of)}: {counts[u]}" for u in order]


def _alert_text(
    result: RunResult,
    line_of: dict[str, int],
    failures_cleared: bool = False,
    ffmpeg_missing: bool = False,
    ytdlp_hint: str | None = None,
) -> tuple[str, str, str]:
    """(title, body, tags). Counts, line numbers, SoundCloud playlist titles and fixed
    text only. Never usernames, links, slugs, track names or error messages, since the
    text goes through a third-party server.

    Tracks that failed inside a playlist that otherwise synced (geo-blocked etc.) are
    listed but still count as a success; "action needed" is for playlists that didn't sync."""
    lines: list[str] = []
    n = result.downloaded
    if n:
        lines.append(f"{n} new track{_s(n)}" + (":" if result.new_tracks else ""))
        lines += _per_playlist(result.new_tracks, result, line_of)
    else:
        lines.append("No new tracks")
    if result.track_errors:
        k = sum(result.track_errors.values())
        lines.append(f"{k} track{_s(k)} couldn't be downloaded (see logs):")
        lines += _per_playlist(result.track_errors, result, line_of)
    if result.stale:
        lines.append(f"Stale link: {_lines_label(result.stale, line_of)} - paste a fresh share link")
    if result.needs_full:
        nums = " ".join(str(n) for n in sorted({line_of[u] for u in result.needs_full if u in line_of}))
        cmd = f" - python src/sync_sc_playlists.py --force-all --only {nums}" if nums else ""
        lines.append(f"Needs a manual full sync: {_lines_label(result.needs_full, line_of)}{cmd}")
    if result.failed:
        lines.append(f"Failed: {_lines_label(result.failed, line_of)} (see logs)")
    elif failures_cleared:
        lines.append("Earlier failures cleared")
    if ffmpeg_missing:
        lines.append("ffmpeg not found on PATH - downloads may fail")
    if ytdlp_hint and (result.failed or result.track_errors):
        lines.append(ytdlp_hint)

    attention = bool(result.stale or result.needs_full or result.failed or ffmpeg_missing)
    title = "SC sync: action needed" if attention else "SC sync: success"
    tags = "warning" if attention else ("musical_note" if result.downloaded else "white_check_mark")
    return title, "\n".join(lines), tags


def _load_last_failed() -> tuple[set[str], set[str]]:
    """(failed playlists, playlists with failed tracks) from the last unattended run."""
    try:
        data = json.loads(LAST_RUN_FILE.read_text(encoding="utf-8"))
        return set(data.get("failed", [])), set(data.get("track_errors", []))
    except Exception:
        return set(), set()


def _alert_result(result: RunResult, ran: list[str], ffmpeg_missing: bool) -> None:
    """Send the end-of-run alert when something happened or needs attention.
    A playlist that keeps failing alerts once, not every day: failures only alert
    when the set of failing playlists (among those run) changed since last time.
    Same for playlists with failed tracks (a geo-blocked track fails every day)."""
    prev_failed, prev_tracks = _load_last_failed()
    ran_set = set(ran)
    now_failed, now_tracks = set(result.failed), set(result.track_errors)
    failures_changed = (prev_failed & ran_set) != now_failed or (prev_tracks & ran_set) != now_tracks
    with contextlib.suppress(OSError):
        LAST_RUN_FILE.write_text(json.dumps({
            "failed": sorted((prev_failed - ran_set) | now_failed),
            "track_errors": sorted((prev_tracks - ran_set) | now_tracks),
        }), encoding="utf-8")

    if not (result.downloaded or result.stale or result.needs_full or ffmpeg_missing or failures_changed):
        print("[notify] nothing new — no alert.")
        return

    hint = ytdlp_update_hint() if result.failed or result.track_errors else None
    line_of = {u: j for j, u in enumerate(_list_urls(), 1)}
    title, body, tags = _alert_text(
        result, line_of,
        failures_cleared=failures_changed and not now_failed and not now_tracks,
        ffmpeg_missing=ffmpeg_missing, ytdlp_hint=hint,
    )
    _notify(title, body, tags)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    global _verbose
    args = parse_args()
    _verbose = args.verbose

    if args.notify_test:
        ok = _notify("SC sync", "Test alert from sync_sc_playlists.py - notifications work.", "white_check_mark")
        sys.exit(0 if ok else 1)

    log_path = _start_log() if args.unattended else None

    lock = _acquire_lock()
    if lock is None:
        print("Another sync is already running — exiting.")
        sys.exit(EXIT_LOCKED)
    _keep_awake()

    if not args.unattended:
        sys.exit(_run(args))

    ffmpeg_missing = _log_header()
    try:
        code = _run(args, ffmpeg_missing=ffmpeg_missing)
    except Exception as e:
        traceback.print_exc()
        # Exception type only: messages can contain archive paths with the username.
        log_name = log_path.name if log_path else "logs"
        _notify("SC sync: crashed", f"Crashed with {type(e).__name__} - see logs/{log_name}", "warning")
        code = 1
    finally:
        lock.close()
    sys.exit(code)


def _run(args, ffmpeg_missing: bool = False) -> int:
    """The sync itself. Returns the exit code."""
    raw_urls = _list_urls()
    if not raw_urls:
        print("No URLs found in sc-playlists-list.md")
        if args.unattended:
            _notify("SC sync: action needed", "No playlist URLs found in sc-playlists-list.md", "warning")
        return 1

    # Resume support
    if args.resume:
        state = _load_state()
        if state and state.get("completed", 0) < len(state.get("urls", [])):
            all_urls = state["urls"]
            done = state["completed"]
            remaining = all_urls[done:]
            print(f"Resuming from {done + 1}/{len(all_urls)} ({len(remaining)} remaining).\n")
            result = sync_list(
                remaining, args.delay, args.max_errors, args.force_all,
                track_state=True, _state_urls=all_urls, _state_offset=done,
                unattended=args.unattended,
            )
            if args.unattended:
                _alert_result(result, remaining, ffmpeg_missing)
            return _finalize(result, unattended=args.unattended)
        print("No interrupted run found — starting fresh.\n")
        _clear_state()

    # Indices = line order in sc-playlists-list.md (line 1 = --only 1, no sorting)
    if args.only:
        only_set = set(args.only)
        to_run = [u for j, u in enumerate(raw_urls, 1) if j in only_set]
    else:
        skip_set: set[int] = set(args.skip or [])
        if args.from_index:
            skip_set |= set(range(1, args.from_index))
        to_run = [u for j, u in enumerate(raw_urls, 1) if j not in skip_set]

    if not to_run:
        print("No playlists to sync after applying filters.")
        return 0

    mode = "full sync (--force-all)" if args.force_all else f"fast-path (sanity {_SANITY_N} + frontier check)"
    print(f"Syncing {len(to_run)}/{len(raw_urls)} playlist(s) — {mode}")
    print(f"  delay: {args.delay}s ±50% | max-errors: {args.max_errors}\n")
    run_set = set(to_run)
    for j, u in enumerate(raw_urls, 1):
        marker = "→" if u in run_set else " "
        print(f"  {marker} {j:2}. {_label(u)}")
    print()

    _save_state(to_run, 0)
    result = sync_list(
        to_run, args.delay, args.max_errors, args.force_all,
        track_state=True, unattended=args.unattended,
    )
    if args.unattended:
        _alert_result(result, to_run, ffmpeg_missing)
    return _finalize(result, unattended=args.unattended)


_FAIL_TAG_RE = re.compile(r"^\[FAIL\]", re.MULTILINE)


def _failed_playlist_stems(urls: list[str]) -> list[str]:
    """Stems of playlists whose .failed file has at least one [FAIL] entry
    (DRM-protected etc.) — re-running scdl won't fix those, but YouTube recovery
    might. [FOUND]/[GO+]/[BLOCKED]/[MONETIZE] entries are left out: retry_failed_with_ytdl.py
    only acts on [FAIL], and a file with only those tags has nothing for it to do."""
    stems = []
    for u in urls:
        failed_path = archive_path(u).with_suffix(".failed")
        if not failed_path.exists():
            continue
        if _FAIL_TAG_RE.search(failed_path.read_text(encoding="utf-8", errors="replace")):
            stems.append(archive_path(u).stem)
    return stems


def _run_youtube_retry(urls: list[str]) -> None:
    stems = _failed_playlist_stems(urls)
    if not stems:
        print("\nNo track-level failures to recover via YouTube.")
        return
    script = _ROOT / "src" / "retry_failed_with_ytdl.py"
    for stem in stems:
        print(f"\n--- {stem} ---")
        subprocess.run([sys.executable, str(script), stem], check=False)


def _report_stale(stale: list[str]) -> None:
    lines = {u: j for j, u in enumerate(_list_urls(), 1)}
    print(f"\n{len(stale)} playlist link(s) look STALE (404 or 0 tracks) — secret link reset or")
    print("playlist made private. Open it on SoundCloud → Share → copy link, and replace the line")
    print("in sc-playlists-list.md (the ?si=... part doesn't matter, the s-<token> does):")
    for u in stale:
        line = f"line {lines[u]:2}" if u in lines else "line ?"
        print(f"  {line}: {_label(u)}\n           {u}")


def _report_needs_full(needs_full: list[str]) -> None:
    lines = {u: j for j, u in enumerate(_list_urls(), 1)}
    print(f"\n{len(needs_full)} playlist(s) not recognised by the quick check (none of the first")
    print(f"{_SANITY_N} tracks are in the archive): reshuffled, or the link now points at another")
    print("playlist. Scheduled runs don't full-sync (it can mark tracks [unsync]); run it yourself:")
    nums = sorted({lines[u] for u in needs_full if u in lines})
    for u in needs_full:
        line = f"line {lines[u]:2}" if u in lines else "line ?"
        print(f"  {line}: {_label(u)}")
    if nums:
        print(f"  python src/sync_sc_playlists.py --force-all --only {' '.join(map(str, nums))}")


def _finalize(result: RunResult, unattended: bool = False) -> int:
    """Print the end-of-run report. Returns the exit code: 0 success (tracks may have
    failed inside playlists that otherwise synced), 1 needs attention."""
    removed = delete_empty(ARCHIVE_DIR)
    if removed:
        print(f"\nRemoved {len(removed)} empty file(s) from archive_trackers/sc.")

    def name(u: str) -> str:
        return result.titles.get(u) or _label(u)

    if result.downloaded:
        print(f"\n{result.downloaded} new track(s) downloaded.")
        for u, n in result.new_tracks.items():
            print(f"  {name(u)}: {n}")
    if result.stale:
        _report_stale(result.stale)
    if result.needs_full:
        _report_needs_full(result.needs_full)
    if result.track_errors:
        k = sum(result.track_errors.values())
        print(f"\n{k} track(s) couldn't be downloaded (geo-blocked, GO+ only, removed, ...). The playlists themselves synced:")
        for u, n in result.track_errors.items():
            print(f"  {name(u)}: {n}")
    if result.failed:
        print(f"\n{len(result.failed)} playlist(s) failed:")
        for u in result.failed:
            print(f"  {u}")

    retry = result.failed + list(result.track_errors)
    if retry and unattended:
        print("\nTo try recovering failed tracks via YouTube, run: python src/retry_failed_with_ytdl.py --all")
    elif retry:
        print("\nTrying to recover failed tracks via YouTube...")
        _run_youtube_retry(retry)

    attention = bool(result.stale or result.needs_full or result.failed)
    if not result.failed:
        print("\nAll done." if not attention else "\nDone — see the playlist(s) above.")
        _clear_state()
    return 1 if attention else 0


if __name__ == "__main__":
    main()
