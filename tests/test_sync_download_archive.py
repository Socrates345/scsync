import logging
from pathlib import Path

from scdl.patches.sync_download_archive import SyncDownloadHelper


class _StubYDL:
    """Just enough of YoutubeDL for SyncDownloadHelper."""

    def __init__(self) -> None:
        self.archive: set[str] = set()
        self.hooks: list = []

    def add_progress_hook(self, hook) -> None:
        self.hooks.append(hook)

    def _make_archive_id(self, info_dict: dict) -> str:
        return f"soundcloud {info_dict['id']}"

    def _match_entry(self, info_dict, incomplete=False, silent=False):
        return "already recorded" if self._make_archive_id(info_dict) in self.archive else None


def _setup(tmp_path: Path, **extra_args) -> tuple[_StubYDL, SyncDownloadHelper, Path, list[Path]]:
    files = [tmp_path / "a.m4a", tmp_path / "b.m4a"]
    for f in files:
        f.write_bytes(b"")
    archive = tmp_path / "pl.txt"
    archive.write_text(f"soundcloud 1 {files[0]}\nsoundcloud 2 {files[1]}\n", encoding="utf-8")
    ydl = _StubYDL()
    helper = SyncDownloadHelper({"sync": str(archive), "path": tmp_path, **extra_args}, ydl)  # type: ignore[arg-type]
    return ydl, helper, archive, files


def test_no_archived_track_seen_keeps_archive(tmp_path: Path, caplog) -> None:
    # Stale URL: the playlist resolved to nothing, so no track was evaluated.
    _, helper, archive, files = _setup(tmp_path)
    before = archive.read_text(encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        helper.post_download()

    assert archive.read_text(encoding="utf-8") == before
    assert all(f.exists() for f in files)
    assert not list(tmp_path.glob("[[]unsync[]]*"))
    assert "Stale playlist URL" in caplog.text


def test_removed_track_is_unsynced(tmp_path: Path, caplog) -> None:
    ydl, helper, archive, files = _setup(tmp_path)
    ydl._match_entry({"id": "1"})  # track 1 still in the playlist, track 2 removed

    with caplog.at_level(logging.WARNING):
        helper.post_download()

    assert archive.read_text(encoding="utf-8").splitlines() == [f"soundcloud 1 {files[0]}"]
    assert files[0].exists()
    assert (tmp_path / "[unsync] b.m4a").exists()
    assert "Stale playlist URL" not in caplog.text


def test_offset_run_keeps_archive_without_warning(tmp_path: Path, caplog) -> None:
    _, helper, archive, _ = _setup(tmp_path, o=3)
    before = archive.read_text(encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        helper.post_download()

    assert archive.read_text(encoding="utf-8") == before
    assert "Stale playlist URL" not in caplog.text
