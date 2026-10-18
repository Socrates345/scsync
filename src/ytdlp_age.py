"""Tell whether the installed yt-dlp is old enough that upgrading is worth it.

Used by ytdl.py (warning at startup) and sync_sc_playlists.py (hint in the
scheduled-run alert when tracks fail).
"""

from __future__ import annotations

import urllib.request
from datetime import date

import yt_dlp.version as _ytdlp_ver

STALE_DAYS = 14
_LATEST_URL = "https://github.com/yt-dlp/yt-dlp/releases/latest"


def _version_tuple(ver: str) -> tuple[int, int, int]:
    year, month, day = (int(x) for x in ver.split(".")[:3])  # e.g. "2026.06.09"
    return year, month, day


def _latest_ytdlp_version() -> str | None:
    """Latest stable yt-dlp release tag, or None if GitHub can't be reached."""
    req = urllib.request.Request(_LATEST_URL, method="HEAD")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            # Redirects to .../releases/tag/<version>
            return resp.url.rstrip("/").rsplit("/", 1)[-1]
    except OSError:
        return None


def ytdlp_update_hint() -> str | None:
    """A one-line upgrade hint, or None if yt-dlp is recent or already the latest."""
    ver = _ytdlp_ver.__version__
    try:
        installed = _version_tuple(ver)
        age = (date.today() - date(*installed)).days
        if age < STALE_DAYS:
            return None
        # Old, but only worth a hint if upgrading would actually change something.
        latest = _latest_ytdlp_version()
        if latest is None or _version_tuple(latest) <= installed:
            return None
    except (ValueError, IndexError):
        return None
    return f"yt-dlp v{ver} is {age} days old and v{latest} is out. Run: uv sync --upgrade-package yt-dlp"
