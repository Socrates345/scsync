#!/usr/bin/env python3
"""Sync all YouTube playlists listed in yt-playlists.md."""

import argparse
import random
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse, parse_qs

_ROOT = Path(__file__).parent.parent
_SRC = Path(__file__).parent
ARCHIVE_DIR = _ROOT / "archive_trackers" / "yt"  # kept for compat
YTDL_SCRIPT = _SRC / "ytdl.py"
_CFG_FILE = _ROOT / "ytdl.cfg"
RATE_LIMIT_COOLDOWN = 120
RETRY_DELAY = 30


def _load_config_cookies() -> str | None:
    import configparser
    cfg = configparser.RawConfigParser()
    cfg.read(_CFG_FILE, encoding="utf-8")
    return cfg.get("ytdl", "cookies_from_browser", fallback=None) or None


def parse_args():
    cfg_cookies = _load_config_cookies()
    p = argparse.ArgumentParser(description="Sync all YouTube playlists in yt-playlists.md / yt-video-playlists.md")
    p.add_argument("--delay", type=float, default=8.0, metavar="SECONDS",
                   help="Base delay between playlists (default: 8s, actual = delay ± 50%% jitter)")
    p.add_argument("--max-errors", type=int, default=5, metavar="N",
                   help="Abort a playlist after N consecutive errors (default: 5)")
    p.add_argument("--video", action="store_true",
                   help="Sync video playlists from yt-video-playlists.md (mp4, 360p unless --res)")
    p.add_argument("--res", metavar="N",
                   help="Max video height forwarded to ytdl.py with --video, e.g. 720 or 'best' "
                        "(default: ytdl.cfg video_res, else 360)")
    p.add_argument("--cookies-from-browser", metavar="BROWSER[:PROFILE]", dest="cookies_from_browser",
                   default=cfg_cookies,
                   help="Browser/profile for cookies forwarded to ytdl.py (overrides ytdl.cfg)")
    p.add_argument("--cookies", metavar="FILE", help="Netscape cookies.txt forwarded to ytdl.py")
    return p.parse_args()


def _archive_name(url: str) -> str:
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    if "list" in qs:
        return qs["list"][0]
    path = parsed.path.strip("/")
    parts = [p for p in path.split("/") if p and p not in ("videos", "playlist", "watch", "shorts")]
    return "_".join(parts).lstrip("@") or "unknown"


def archive_path(url: str, video: bool = False) -> Path:
    adir = _ROOT / "archive_trackers" / ("yt-video" if video else "yt")
    id_ = _archive_name(url)
    for f in adir.glob(f"*_{id_}.txt"):
        return f
    return adir / f"{id_}.txt"


def is_empty(path: Path) -> bool:
    return not path.exists() or path.stat().st_size == 0


def sort_urls(urls: list[str], video: bool = False) -> list[str]:
    """Empty/missing archives first (both groups shuffled internally)."""
    empty, populated = [], []
    for url in urls:
        (empty if is_empty(archive_path(url, video)) else populated).append(url)
    random.shuffle(empty)
    random.shuffle(populated)
    return empty + populated


def run_ytdl(url: str, max_errors: int, extra_args: list[str]) -> tuple[bool, bool, list[str]]:
    cmd = [sys.executable, str(YTDL_SCRIPT), "-l", url, "--sync"] + extra_args
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    has_error = False
    consecutive_errors = 0
    aborted = False
    error_lines: list[str] = []

    for line in proc.stdout:
        print(line, end="", flush=True)
        if line.startswith("ERROR:"):
            has_error = True
            consecutive_errors += 1
            error_lines.append(line.rstrip())
            if consecutive_errors >= max_errors:
                print(
                    f"\n  [sync] {consecutive_errors} consecutive errors — "
                    f"rate limited. Aborting playlist early.",
                    flush=True,
                )
                proc.terminate()
                try:
                    proc.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                aborted = True
                break
        elif "[download]" in line and "%" in line:
            consecutive_errors = 0

    proc.wait()
    success = proc.returncode == 0 and not has_error
    return success, aborted, error_lines


def errors_log_path(url: str, video: bool = False) -> Path:
    return archive_path(url, video).with_suffix(".errors.log")


def sync_list(
    urls: list[str],
    delay: float,
    max_errors: int,
    extra_args: list[str],
    label: str = "",
    video: bool = False,
) -> list[str]:
    failed = []
    total = len(urls)

    for i, url in enumerate(urls, 1):
        prefix = f"{label}[{i}/{total}]" if label else f"[{i}/{total}]"
        print(f"{prefix} {url}")

        ok, aborted, error_lines = run_ytdl(url, max_errors, extra_args)

        if not ok:
            failed.append(url)

        if error_lines:
            log = errors_log_path(url, video)
            log.parent.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with log.open("a", encoding="utf-8") as f:
                f.write(f"\n--- {timestamp}  {url} ---\n")
                for line in error_lines:
                    f.write(line + "\n")

        print()

        if i < total:
            if aborted:
                print(f"  [sync] Rate limit — cooling down {RATE_LIMIT_COOLDOWN}s before next playlist...\n")
                time.sleep(RATE_LIMIT_COOLDOWN)
            elif delay > 0:
                actual = delay * random.uniform(0.5, 1.5)
                remaining = total - i
                print(f"  Waiting {actual:.1f}s before next playlist ({remaining} remaining)...\n")
                time.sleep(actual)

    return failed


def _print_error_log_summary(urls: list[str], video: bool = False) -> None:
    logs = [errors_log_path(url, video) for url in urls if errors_log_path(url, video).exists()]
    if not logs:
        return
    print(f"\n{len(logs)} playlist(s) have error logs:")
    for log in logs:
        print(f"  {log}")


def main():
    args = parse_args()

    playlist_file = _SRC / ("yt-video-playlists.md" if args.video else "yt-playlists.md")
    mode_label = "video" if args.video else "audio"

    if not playlist_file.exists():
        print(f"No playlist file found at {playlist_file}")
        fname = "yt-video-playlists.md" if args.video else "yt-playlists.md"
        print(f"Create {fname} with one YouTube URL per line.")
        sys.exit(1)

    raw_urls = [
        line.strip()
        for line in playlist_file.read_text(encoding="utf-8").splitlines()
        if line.strip().startswith("http")
    ]

    if not raw_urls:
        print(f"No URLs found in {playlist_file.name}")
        sys.exit(1)

    urls = sort_urls(raw_urls, args.video)

    extra_args: list[str] = []
    if args.video:
        extra_args += ["--video"]
        if args.res:
            extra_args += ["--res", args.res]
    if args.cookies:
        extra_args += ["--cookies", args.cookies]
    elif args.cookies_from_browser:
        extra_args += ["--cookies-from-browser", args.cookies_from_browser]

    empty_count = sum(1 for u in urls if is_empty(archive_path(u, args.video)))
    print(
        f"Syncing {len(urls)} YouTube {mode_label} playlists "
        f"({empty_count} new first, then {len(urls) - empty_count} existing, both shuffled).\n"
        f"Delay: {args.delay}s ± 50% jitter | max-errors: {args.max_errors}\n"
    )

    failed = sync_list(urls, args.delay, args.max_errors, extra_args, video=args.video)

    if not failed:
        print(f"Done. All {len(urls)} playlists synced successfully.")
        _print_error_log_summary(urls, args.video)
        return

    print(f"\n{len(failed)} playlist(s) failed. Waiting 60s before retry...\n")
    time.sleep(60)

    still_failed = sync_list(failed, RETRY_DELAY, args.max_errors, extra_args, label="RETRY ", video=args.video)

    if still_failed:
        print(f"\nDone. {len(still_failed)} playlist(s) failed after retry:")
        for url in still_failed:
            print(f"  {url}")
        _print_error_log_summary(urls, args.video)
        sys.exit(1)
    else:
        print(f"\nDone. All playlists synced successfully (some needed a retry).")
        _print_error_log_summary(urls, args.video)


if __name__ == "__main__":
    main()
