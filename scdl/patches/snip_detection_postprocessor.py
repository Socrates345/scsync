from pathlib import Path
from typing import Callable

import mutagen
from yt_dlp.postprocessor.common import PostProcessor

MAX_SNIP_DURATION = 30  # seconds


class SnipDetectionPP(PostProcessor):
    """Catches SoundCloud GO+ preview snips that the `duration>30` match-filter
    misses: SoundCloud's metadata reports the full track's length even when the
    actual stream served (without a GO+ subscription) is a ~30s preview, so the
    filter — which only ever sees that metadata field — lets them through. This
    runs after the file is in its final form/location and checks the real,
    decoded audio length instead."""

    def __init__(self, downloader=None, on_snip: "Callable[[dict, float], None] | None" = None):
        super().__init__(downloader)
        self._on_snip = on_snip

    def run(self, info: dict):
        filepath = info.get("filepath")
        if not filepath:
            return [], info
        path = Path(filepath)
        if not path.is_file():
            return [], info

        try:
            audio = mutagen.File(path)
        except Exception:
            return [], info
        if audio is None or audio.info is None:
            return [], info

        duration = audio.info.length
        if duration > MAX_SNIP_DURATION:
            return [], info

        self.to_screen(f'Discarding "{path.name}" — {duration:.1f}s GO+ preview snip, not the full track')
        path.unlink(missing_ok=True)
        if self._on_snip:
            self._on_snip(info, duration)
        return [], info
