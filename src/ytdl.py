#!/usr/bin/env python3
"""YouTube playlist/channel downloader with sync support, built on yt-dlp.

Usage:
    python ytdl.py -l URL [--sync] [--video [--res N|best]] [--path PATH]
                   [--no-playlist-folder]
                   [--cookies-from-browser BROWSER[:PROFILE]] [--cookies FILE]
                   [--overwrite] [--debug] [--yt-dlp-args ARGSTRING]

Config: copy ytdl.cfg.example to ytdl.cfg and set cookies_from_browser.
Video downloads are capped at 360p unless --res / ytdl.cfg video_res says otherwise.
"""

from __future__ import annotations

import argparse
import configparser
import logging
import shlex
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from yt_dlp import YoutubeDL
from yt_dlp.utils import sanitize_filename

from scdl import utils
from scdl.patches.sync_download_archive import SyncDownloadHelper
from ytdlp_age import ytdlp_update_hint

logging.setLoggerClass(utils.YTLogger)
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logger.addHandler(logging.StreamHandler())

_DEFAULT_VIDEO_RES = "360"


def _check_ytdlp_age() -> None:
    hint = ytdlp_update_hint()
    if hint:
        logger.warning(f"[ytdl] {hint}")


_ROOT = Path(__file__).parent.parent
_ARCHIVE_BASE = _ROOT / "archive_trackers"
ARCHIVE_DIR = _ARCHIVE_BASE / "yt"  # kept for external references
_CFG_FILE = _ROOT / "ytdl.cfg"


def _archive_dir(video: bool) -> Path:
    return _ARCHIVE_BASE / ("yt-video" if video else "yt")


def _load_config() -> configparser.RawConfigParser:
    cfg = configparser.RawConfigParser()
    cfg.read(_CFG_FILE, encoding="utf-8")
    return cfg


def _archive_path(url: str, video: bool = False) -> Path:
    adir = _archive_dir(video)
    id_ = _archive_name(url)
    for f in adir.glob(f"*_{id_}.txt"):
        return f
    return adir / f"{id_}.txt"


def _rename_archive_with_title(path: Path, title: str) -> None:
    """Prefix archive file (and companions) with playlist title after first download."""
    if not path.exists():
        return
    safe = sanitize_filename(title, restricted=False)
    if path.stem.startswith(safe):
        return
    new_path = path.parent / f"{safe}_{path.stem}.txt"
    path.rename(new_path)
    for ext in (".failed", ".errors.log"):
        old = path.with_suffix(ext)
        if old.exists():
            old.rename(new_path.with_suffix(ext))


def _parse_res(raw: str) -> int | None:
    """Max video height for --video ("360", "720p", ...). "best" or 0 means no cap."""
    value = raw.strip().lower().removesuffix("p")
    if value == "best":
        return None
    height = int(value)
    if height < 0:
        raise ValueError(raw)
    return height or None


def _archive_name(url: str) -> str:
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    if "list" in qs:
        return qs["list"][0]
    path = parsed.path.strip("/")
    parts = [p for p in path.split("/") if p and p not in ("videos", "playlist", "watch", "shorts")]
    return "_".join(parts).lstrip("@") or "unknown"


def download(url: str, args: argparse.Namespace) -> None:
    video = getattr(args, "video", False)
    base = Path(args.path).resolve()
    adir = _archive_dir(video)
    adir.mkdir(parents=True, exist_ok=True)

    sync_file = str(_archive_path(url, video))

    scdl_args: dict = {
        "sync": sync_file if args.sync else None,
        "no_playlist_folder": args.no_playlist_folder,
        "path": base,
    }

    if args.no_playlist_folder:
        outtmpl = str(base / "%(title)s.%(ext)s")
    else:
        outtmpl = str(base / "%(playlist|)s" / "%(title)s.%(ext)s")

    if video:
        argv = [
            "--format", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best",
            "--merge-output-format", "mp4",
            "--embed-metadata",
            "--embed-thumbnail",
            "--output-na-placeholder", "",
            "--trim-filenames", "240b",
            "--sleep-requests", "1",
            "--extractor-retries", "5",
            "--retry-sleep", "extractor:60",
            "--js-runtimes", "node",
            "--remote-components", "ejs:github",
        ]
        res = getattr(args, "res", None)
        if res:
            # Prefer the best stream at or below this height; falls back to the
            # smallest one above it if the video has nothing that low.
            argv += ["--format-sort", f"res:{res}"]
    else:
        argv = [
            "--format", "bestaudio[ext=m4a]/bestaudio/best",
            "--embed-metadata",
            "--embed-thumbnail",
            "--remux-video", "aac>m4a",
            "--output-na-placeholder", "",
            "--trim-filenames", "240b",
            "--sleep-requests", "1",
            "--extractor-retries", "5",
            "--retry-sleep", "extractor:60",
            "--js-runtimes", "node",
            "--remote-components", "ejs:github",
        ]

    if args.offset:
        argv += ["--playlist-items", f"{args.offset}:"]

    if not args.sync:
        argv += ["--break-on-existing"]

    if args.overwrite:
        argv += ["--force-overwrites"]

    if getattr(args, "cookies", None):
        argv += ["--cookies", args.cookies]
    elif args.cookies_from_browser:
        argv += ["--cookies-from-browser", args.cookies_from_browser]

    if args.debug:
        argv += ["--verbose"]

    params = utils.cli_to_api(argv)
    params["outtmpl"] = outtmpl
    params["logger"] = logger

    if args.yt_dlp_args:
        overrides = utils.cli_to_api(shlex.split(args.yt_dlp_args))
        params = {**params, **overrides}

    if args.sync:
        logger.info(f"[ytdl] Sync archive: {sync_file}")

    with YoutubeDL(params) as ydl:
        sync = SyncDownloadHelper(scdl_args, ydl)
        ydl.download([url])
        sync.post_download()
        if sync._playlist_title:
            _rename_archive_with_title(Path(sync_file), sync._playlist_title)


def _resolve_cfg_path(raw: str | None, fallback: str) -> str:
    if raw:
        p = Path(raw)
        return str(p if p.is_absolute() else _ROOT / p)
    return str(_ROOT / fallback)


def main() -> None:
    _check_ytdlp_age()
    cfg = _load_config()
    cfg_cookies = cfg.get("ytdl", "cookies_from_browser", fallback=None) or None

    cfg_res = cfg.get("ytdl", "video_res", fallback=None) or _DEFAULT_VIDEO_RES

    # Pre-parse --video so we can select the right default path before building the full parser.
    _pre = argparse.ArgumentParser(add_help=False)
    _pre.add_argument("--video", action="store_true", default=False)
    _pre_args, _ = _pre.parse_known_args()

    if _pre_args.video:
        cfg_path_str = _resolve_cfg_path(
            cfg.get("ytdl", "video_path", fallback=None) or None,
            "playlists/yt-video",
        )
    else:
        cfg_path_str = _resolve_cfg_path(
            cfg.get("ytdl", "path", fallback=None) or None,
            "playlists/yt",
        )

    p = argparse.ArgumentParser(
        description="YouTube playlist/channel syncer built on yt-dlp",
        epilog="Config defaults come from ytdl.cfg (copy from ytdl.cfg.example).",
    )
    p.add_argument("-l", required=True, metavar="URL", help="YouTube playlist/channel/video URL")
    p.add_argument("--sync", action="store_true",
                   help="Download new tracks, mark removed ones as [unsync]")
    p.add_argument("--video", action="store_true",
                   help="Download video+audio (mp4) instead of audio-only (m4a)")
    p.add_argument("--res", type=_parse_res, metavar="N", default=cfg_res,
                   help="Max video height for --video, e.g. 360, 720, or 'best' for no cap "
                        f"(default from ytdl.cfg video_res, else {_DEFAULT_VIDEO_RES})")
    p.add_argument("--path", default=cfg_path_str, metavar="PATH",
                   help="Download directory (default from ytdl.cfg / ytdl.cfg video_path)")
    p.add_argument("-o", "--offset", type=int, metavar="N", default=None,
                   help="Start from item N in the playlist (skips items 1 to N-1)")
    p.add_argument("--no-playlist-folder", action="store_true", dest="no_playlist_folder",
                   help="Download into PATH directly, no playlist subfolder")
    p.add_argument("--overwrite", action="store_true", help="Overwrite existing files")
    p.add_argument("--cookies-from-browser", metavar="BROWSER[:PROFILE]", dest="cookies_from_browser",
                   default=cfg_cookies,
                   help="Browser/profile for cookies (overrides ytdl.cfg)")
    p.add_argument("--cookies", metavar="FILE", help="Netscape-format cookies.txt")
    p.add_argument("--debug", action="store_true")
    p.add_argument("--yt-dlp-args", metavar="ARGSTRING", dest="yt_dlp_args",
                   help="Extra yt-dlp arguments forwarded as-is")
    args = p.parse_args()
    download(args.l, args)


if __name__ == "__main__":
    main()
