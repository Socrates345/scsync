import contextlib
import errno
import logging
from functools import partial
from pathlib import Path

from yt_dlp import YoutubeDL
from yt_dlp.utils import locked_file, sanitize_filename

from scdl.patches.archive_paths import to_absolute, to_relative

logger = logging.getLogger(__name__)


class SyncDownloadHelper:
    def __init__(self, scdl_args, ydl: YoutubeDL):
        self._ydl = ydl
        self._scdl_args = scdl_args
        self._enabled = bool(scdl_args.get("sync"))
        self._sync_file = scdl_args.get("sync")
        self._all_files: dict[str, Path] = {}
        self._downloaded: set[str] = set()
        self._preexisting: set[str] = set()  # IDs loaded from archive before this run
        self._attempted: dict[str, str] = {}  # archive_id -> track URL
        self._playlist_title: str | None = None
        self._init()

    def _init(self):
        if not self._enabled:
            return

        # track downloaded ids/filenames
        def track_downloaded(d):
            if d["status"] != "finished":
                return
            info = d["info_dict"]
            id_ = self._ydl._make_archive_id(info)
            if id_ is None:
                return
            self._downloaded.add(id_)
            self._all_files[id_] = d["filename"]

        self._ydl.add_progress_hook(track_downloaded)

        # add already downloaded files to the archive
        try:
            with locked_file(self._sync_file, "r", encoding="utf-8") as archive_file:
                for line in archive_file:
                    line = line.strip()
                    if not line:
                        continue
                    ie, id_, filename = line.split(maxsplit=2)
                    key = f"{ie} {id_}"
                    self._ydl.archive.add(key)
                    self._all_files[key] = to_absolute(filename)
                    self._preexisting.add(key)
        except OSError as ioe:
            if ioe.errno != errno.ENOENT:
                raise

        # track ids checked against the archive; capture info for new tracks being attempted
        old_match_entry = self._ydl._match_entry

        def _match_entry(ydl, info_dict, incomplete=False, silent=False):
            archive_id = ydl._make_archive_id(info_dict)
            self._downloaded.add(archive_id)
            if self._playlist_title is None and info_dict.get("playlist"):
                self._playlist_title = info_dict["playlist"]
            result = old_match_entry(info_dict, incomplete, silent)
            # result is None → track passed all filters and is not in archive → being attempted.
            # Called twice per track (incomplete=True then incomplete=False); overwrite so the
            # richer call wins.
            if result is None:
                self._attempted[archive_id] = (
                    info_dict.get("webpage_url")
                    or info_dict.get("permalink_url")
                    or info_dict.get("original_url")
                    or info_dict.get("url")
                    or ""
                )
            return result

        self._ydl._match_entry = partial(_match_entry, self._ydl)

    def discard(self, archive_id: str) -> None:
        """Drop a track from this run's tracked state without touching the live
        yt-dlp archive — used when a file downloaded successfully but was later
        found to be invalid (e.g. a GO+ preview snip) and deleted, so it isn't
        written to the sync archive or counted as a slot the playlist lost."""
        self._downloaded.discard(archive_id)
        self._all_files.pop(archive_id, None)
        self._attempted.pop(archive_id, None)

    def _check_playlist_rename(self) -> None:
        """If the playlist was renamed, move files from the old folder to the new one and update archive paths."""
        if not self._all_files or not self._playlist_title:
            return
        if self._scdl_args.get("no_playlist_folder"):
            return

        base = Path(self._scdl_args["path"])
        new_folder_name = sanitize_filename(self._playlist_title, restricted=False)
        if not new_folder_name:
            return
        new_folder = base / new_folder_name

        # Collect unique old folders from archive paths (normally just one)
        old_folders: set[Path] = set()
        for path in self._all_files.values():
            p = Path(path)
            if p.parent != base:
                old_folders.add(p.parent)

        moved: dict[Path, Path] = {}  # old_folder -> new_folder (for path rewriting)
        for old_folder in old_folders:
            if old_folder == new_folder or not old_folder.exists():
                continue
            logger.info(f"[scdl] Playlist renamed: {old_folder.name!r} → {new_folder_name!r}, moving files…")
            new_folder.mkdir(parents=True, exist_ok=True)
            for src in sorted(old_folder.iterdir()):
                dst = new_folder / src.name
                if not dst.exists():
                    src.rename(dst)
                    logger.info(f"[scdl]   moved {src.name}")
            with contextlib.suppress(OSError):
                old_folder.rmdir()
            moved[old_folder] = new_folder

        if moved:
            self._all_files = {
                k: str(moved[Path(v).parent] / Path(v).name) if Path(v).parent in moved else v
                for k, v in self._all_files.items()
            }

    def post_download(self):
        if not self._enabled:
            return

        self._check_playlist_rename()

        # When -o (offset) is active, yt-dlp never evaluates tracks before the offset
        # position, so they never appear in self._downloaded. Don't treat them as removed
        # from the playlist — preserve them in the archive as-is.
        offset = self._scdl_args.get("o")
        not_evaluated = self._preexisting - self._downloaded if offset else set()

        # None of the archived tracks showed up: the URL most likely no longer points at
        # this playlist (secret link reset, playlist made private → 404 / 0 tracks).
        # Don't treat the whole archive as removed — keep it and skip [unsync].
        if not offset and self._preexisting and not (self._preexisting & self._downloaded):
            logger.warning(
                f"[scdl] Stale playlist URL? None of the {len(self._preexisting)} archived tracks "
                "were found — skipping [unsync]. Get a fresh share link."
            )
            not_evaluated = self._preexisting - self._downloaded

        # rename files for tracks no longer in the playlist
        to_unsync = {
            key: self._all_files[key]
            for key in (set(self._all_files.keys()) - self._downloaded - not_evaluated)
        }
        for filepath in to_unsync.values():
            filepath = Path(filepath)
            if filepath.exists() and not filepath.name.startswith("[unsync] "):
                filepath.rename(filepath.parent / f"[unsync] {filepath.name}")

        with locked_file(self._sync_file, "w", encoding="utf-8") as archive_file:
            for k, v in self._all_files.items():
                if k in self._downloaded or k in not_evaluated:
                    archive_file.write(f"{k} {to_relative(v)}\n")

        failed_file = Path(self._sync_file).with_suffix(".failed")
        with locked_file(str(failed_file), "w", encoding="utf-8") as f:
            for archive_id, url in self._attempted.items():
                if archive_id not in self._all_files:
                    f.write(f"{archive_id} {url}\n")
