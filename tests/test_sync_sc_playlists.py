"""Tests for src/sync_sc_playlists.py: unattended-run routing, lock, alerts.

src/ isn't a package, so the module is loaded from its file path.
"""

import importlib.util
import sys
from pathlib import Path
from typing import ClassVar

import pytest
import yt_dlp

_SRC =Path(__file__).resolve().parent.parent / "src"


def _load():
    if str(_SRC) not in sys.path:
        sys.path.insert(0, str(_SRC))
    spec = importlib.util.spec_from_file_location("sync_sc_playlists", _SRC / "sync_sc_playlists.py")
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ssp = _load()

URL = "https://soundcloud.com/someone/sets/mix/s-tok123"


def _quiet(_line: str) -> None:
    pass


@pytest.fixture
def playlist(tmp_path, monkeypatch):
    """One listed playlist whose archive holds tracks 1..5."""
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    monkeypatch.setattr(ssp, "ARCHIVE_DIR", archive_dir)
    plist = tmp_path / "list.md"
    plist.write_text(URL + "\n", encoding="utf-8")
    monkeypatch.setattr(ssp, "PLAYLIST_FILE", plist)
    ssp.archive_path(URL).write_text(
        "".join(f"soundcloud {i} playlists/sc/mix/{i}.m4a\n" for i in range(1, 6)), encoding="utf-8"
    )
    return URL


def _fetch(heads, next_ids):
    """Fake _fetch_ids_at: `heads` for the 1:N sanity range, `next_ids` for position N+1."""

    def fetch(_url, items):
        return heads if items.startswith("1:") else next_ids

    return fetch


class _NoPopen:
    def __init__(self, *_args, **_kwargs) -> None:
        msg = "scdl must not be started"
        raise AssertionError(msg)


class _FakeProc:
    calls: ClassVar[list] = []

    def __init__(self, cmd, **kwargs) -> None:
        _FakeProc.calls.append((cmd, kwargs))
        self.stdout = iter(["[download] Destination: x.m4a\n"])
        self.returncode = 0

    def wait(self) -> int:
        return 0


@pytest.fixture
def fake_proc(monkeypatch):
    _FakeProc.calls = []
    monkeypatch.setattr(ssp.subprocess, "Popen", _FakeProc)
    return _FakeProc


# ---------------------------------------------------------------------------
# run_scdl routing
# ---------------------------------------------------------------------------


def test_unattended_mismatch_is_flagged_not_full_synced(playlist, monkeypatch):
    monkeypatch.setattr(ssp, "_fetch_ids_at", _fetch(["97", "98", "99"], ["100"]))
    monkeypatch.setattr(ssp.subprocess, "Popen", _NoPopen)
    ok, _, _, _, stats = ssp.run_scdl(playlist, 5, write=_quiet, unattended=True)
    assert ok
    assert stats["needs_full"]
    assert stats["skipped"]


@pytest.mark.parametrize(("heads", "next_ids"), [(None, ["6"]), (["1", "2", "3"], None)])
def test_unattended_scan_error_is_a_failure(playlist, monkeypatch, heads, next_ids):
    monkeypatch.setattr(ssp, "_fetch_ids_at", _fetch(heads, next_ids))
    monkeypatch.setattr(ssp.subprocess, "Popen", _NoPopen)
    ok, _, _, _, stats = ssp.run_scdl(playlist, 5, write=_quiet, unattended=True)
    assert not ok
    assert stats["scan_failed"]


def test_manual_mismatch_still_full_syncs(playlist, monkeypatch, fake_proc):
    monkeypatch.setattr(ssp, "_fetch_ids_at", _fetch(["97", "98", "99"], ["100"]))
    ok, _, _, _, stats = ssp.run_scdl(playlist, 5, write=_quiet)
    cmd, kwargs = fake_proc.calls[0]
    assert "-o" not in cmd
    assert kwargs["cwd"] == ssp._ROOT
    assert ok
    assert stats["downloaded"] == 1


def test_new_tracks_start_after_archive(playlist, monkeypatch, fake_proc):
    monkeypatch.setattr(ssp, "_fetch_ids_at", _fetch(["1", "2", "3"], ["6"]))
    ssp.run_scdl(playlist, 5, write=_quiet, unattended=True)
    cmd, kwargs = fake_proc.calls[0]
    assert cmd[cmd.index("-o") + 1] == "6"
    assert kwargs["cwd"] == ssp._ROOT


def test_up_to_date_playlist_is_skipped(playlist, monkeypatch):
    monkeypatch.setattr(ssp, "_fetch_ids_at", _fetch(["1", "2", "3"], []))
    monkeypatch.setattr(ssp.subprocess, "Popen", _NoPopen)
    ok, _, _, _, stats = ssp.run_scdl(playlist, 5, write=_quiet, unattended=True)
    assert ok
    assert stats["skipped"]
    assert not stats["needs_full"]


class _FakeYDL:
    answer: ClassVar[object] = None

    def __init__(self, _params) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_exc) -> None:
        pass

    def extract_info(self, _url, download=False):
        assert not download
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ({"title": "Inbox", "entries": []}, []),
        ({"entries": [{"id": 7}, None, {"id": 8}]}, ["7", "8"]),
        (Exception("Unable to download JSON metadata: HTTP Error 404: Not Found"), ssp._GONE),
        (Exception("HTTP Error 500: Internal Server Error"), None),
        (None, None),
    ],
)
def test_fetch_ids_tells_empty_playlist_from_dead_link(monkeypatch, answer, expected):
    monkeypatch.setattr(_FakeYDL, "answer", answer)
    monkeypatch.setattr(yt_dlp, "YoutubeDL", _FakeYDL)
    assert ssp._fetch_ids_at(URL, "1:3") == expected


@pytest.mark.parametrize("force_all", [False, True])
def test_empty_playlist_is_a_warning_not_a_dead_link(playlist, monkeypatch, force_all):
    archive = ssp.archive_path(playlist)
    archive.with_suffix(".failed").write_text("[FAIL] 3 x\n", encoding="utf-8")
    archive.with_suffix(".premium").write_text("4 y\n", encoding="utf-8")
    monkeypatch.setattr(ssp, "_fetch_ids_at", _fetch([], []))
    monkeypatch.setattr(ssp.subprocess, "Popen", _NoPopen)
    ok, _, _, _, stats = ssp.run_scdl(playlist, 5, force_all, write=_quiet, unattended=True)
    assert ok
    assert stats["empty"]
    assert not stats["stale"]
    assert stats["cleared"] == 5
    assert not archive.exists()
    assert not archive.with_suffix(".failed").exists()
    assert archive.with_suffix(".premium").exists()  # append-only GO+ log, kept


def test_refilled_playlist_syncs_as_new(playlist, monkeypatch, fake_proc):
    """Tracks added after the playlist was empty are all new: a scheduled run
    downloads them (first full sync) instead of asking for a manual one."""
    monkeypatch.setattr(ssp, "_fetch_ids_at", _fetch([], []))
    ssp.run_scdl(playlist, 5, write=_quiet, unattended=True)
    monkeypatch.setattr(ssp, "_fetch_ids_at", _fetch(["97", "98"], []))
    ok, _, _, _, stats = ssp.run_scdl(playlist, 5, write=_quiet, unattended=True)
    cmd, _ = fake_proc.calls[0]
    assert "-o" not in cmd
    assert ok
    assert not stats["needs_full"]


def test_404_is_still_a_stale_link(playlist, monkeypatch):
    monkeypatch.setattr(ssp, "_fetch_ids_at", _fetch(ssp._GONE, ssp._GONE))
    monkeypatch.setattr(ssp.subprocess, "Popen", _NoPopen)
    ok, _, _, _, stats = ssp.run_scdl(playlist, 5, write=_quiet, unattended=True)
    assert not ok
    assert stats["stale"]
    assert not stats["empty"]
    assert ssp.archive_path(playlist).exists()


def test_empty_playlist_counts_as_synced(playlist, monkeypatch, tmp_path):
    monkeypatch.setattr(ssp, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(ssp, "_fetch_ids_at", _fetch([], []))
    monkeypatch.setattr(ssp.subprocess, "Popen", _NoPopen)
    result = ssp.sync_list([playlist], 0, 5, force_all=False, unattended=True)
    assert result.empty == [playlist]
    assert result.stale == []
    assert result.failed == []
    assert ssp._finalize(result, unattended=True) == 0


def test_unattended_stops_after_repeated_scan_errors(tmp_path, monkeypatch):
    urls = [f"https://soundcloud.com/someone/sets/mix-{i}" for i in range(4)]
    monkeypatch.setattr(ssp, "ARCHIVE_DIR", tmp_path)
    monkeypatch.setattr(ssp, "PLAYLIST_FILE", tmp_path / "list.md")
    (tmp_path / "list.md").write_text("\n".join(urls), encoding="utf-8")
    fetched: list[str] = []

    def fetch(url, _items):
        fetched.append(url)

    monkeypatch.setattr(ssp, "_fetch_ids_at", fetch)
    result = ssp.sync_list(urls, 0, 5, force_all=False, unattended=True)
    assert result.failed == urls
    assert urls[3] not in fetched


# ---------------------------------------------------------------------------
# Lock
# ---------------------------------------------------------------------------


def test_lock_is_exclusive(tmp_path):
    path = tmp_path / ".sync.lock"
    first = ssp._acquire_lock(path)
    assert first is not None
    try:
        assert ssp._acquire_lock(path) is None
    finally:
        first.close()
    again = ssp._acquire_lock(path)
    assert again is not None
    again.close()


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------

_SECRET_URLS = [f"https://soundcloud.com/zq9user/sets/secret-mix-{i}/s-AbC{i}tokenXYZ?si=deadbeef" for i in range(1, 8)]


def test_alert_text_has_no_identifiers():
    line_of = {u: i for i, u in enumerate(_SECRET_URLS, 1)}
    result = ssp.RunResult(
        downloaded=5,
        failed=_SECRET_URLS[0:3],
        stale=[_SECRET_URLS[4]],
        empty=[_SECRET_URLS[5]],
        needs_full=[_SECRET_URLS[6]],
    )
    title, body, tags = ssp._alert_text(result, line_of, ffmpeg_missing=True, ytdlp_hint="yt-dlp is old")
    for leak in ("zq9user", "secret-mix", "tokenXYZ", "AbC", "deadbeef", "soundcloud", "http"):
        assert leak not in title
        assert leak not in body
    assert "5 new tracks" in body
    assert "Failed: lines 1-3" in body
    assert "Warning: stale link on line 5" in body
    assert "Empty playlist: line 6" in body
    assert "--force-all --only 7" in body
    assert "yt-dlp is old" in body
    assert tags == "warning"
    title.encode("latin-1")  # goes in an HTTP header


def test_alert_text_quiet_cases():
    title, body, tags = ssp._alert_text(ssp.RunResult(downloaded=1), {})
    assert (title, body, tags) == ("SC sync: success", "1 new track", "musical_note")
    _, body, _ = ssp._alert_text(ssp.RunResult(), {}, failures_cleared=True)
    assert body == "No new tracks\nEarlier failures cleared"


def test_alert_track_errors_are_still_a_success():
    u = _SECRET_URLS
    line_of = {x: i for i, x in enumerate(u, 1)}
    result = ssp.RunResult(
        downloaded=3,
        new_tracks={u[1]: 3},
        track_errors={u[3]: 1, u[0]: 4},
        titles={u[0]: "Metal", u[1]: "Rap FR"},
    )
    title, body, tags = ssp._alert_text(result, line_of, ytdlp_hint="yt-dlp is old")
    assert title == "SC sync: success"
    assert tags == "musical_note"
    assert body == (
        "3 new tracks:\n- Rap FR: 3\n"
        "5 tracks couldn't be downloaded (see logs):\n- Metal: 4\n- line 4: 1\n"
        "yt-dlp is old"
    )
    for leak in ("zq9user", "secret-mix", "tokenXYZ", "AbC", "deadbeef", "soundcloud", "http"):
        assert leak not in body

    title, body, tags = ssp._alert_text(
        ssp.RunResult(track_errors={u[0]: 1}, titles={u[0]: "Metal"}), line_of)
    assert (title, tags) == ("SC sync: success", "white_check_mark")
    assert body == "No new tracks\n1 track couldn't be downloaded (see logs):\n- Metal: 1"


_EMPTY_LINE = "Empty playlist: line 3 - synced OK, nothing to download"


def test_alert_empty_playlist_is_a_success():
    line_of = {u: i for i, u in enumerate(_SECRET_URLS, 1)}
    title, body, tags = ssp._alert_text(ssp.RunResult(empty=[_SECRET_URLS[2]]), line_of)
    assert (title, tags) == ("SC sync: success", "white_check_mark")
    assert body == f"No new tracks\n{_EMPTY_LINE}"


def test_empty_playlist_alerts_once(tmp_path, monkeypatch):
    monkeypatch.setattr(ssp, "ytdlp_update_hint", lambda: None)
    monkeypatch.setattr(ssp, "LAST_RUN_FILE", tmp_path / "last.json")
    plist = tmp_path / "list.md"
    plist.write_text("\n".join(_SECRET_URLS), encoding="utf-8")
    monkeypatch.setattr(ssp, "PLAYLIST_FILE", plist)
    sent: list[tuple[str, str]] = []

    def notify(title, body, _tags=""):
        sent.append((title, body))
        return True

    monkeypatch.setattr(ssp, "_notify", notify)
    u = _SECRET_URLS[2]
    ran = [u]
    ssp._alert_result(ssp.RunResult(empty=[u]), ran, ffmpeg_missing=False)
    ssp._alert_result(ssp.RunResult(empty=[u]), ran, ffmpeg_missing=False)
    assert sent == [("SC sync: success", f"No new tracks\n{_EMPTY_LINE}")]
    # refilled, then emptied again: warned again
    ssp._alert_result(ssp.RunResult(downloaded=2, new_tracks={u: 2}), ran, ffmpeg_missing=False)
    ssp._alert_result(ssp.RunResult(empty=[u]), ran, ffmpeg_missing=False)
    assert len(sent) == 3
    assert sent[-1] == ("SC sync: success", f"No new tracks\n{_EMPTY_LINE}")


_STALE_LINE = "Warning: stale link on line 3 - paste a fresh share link"


def test_alert_stale_link_is_a_warning_not_action_needed():
    line_of = {u: i for i, u in enumerate(_SECRET_URLS, 1)}
    title, body, tags = ssp._alert_text(ssp.RunResult(stale=[_SECRET_URLS[2]]), line_of)
    assert (title, tags) == ("SC sync: success", "white_check_mark")
    assert body == f"No new tracks\n{_STALE_LINE}"


def test_stale_link_alerts_once(tmp_path, monkeypatch):
    monkeypatch.setattr(ssp, "ytdlp_update_hint", lambda: None)
    last = tmp_path / "last.json"
    last.write_text('{"failed": [], "empty": []}', encoding="utf-8")  # file from before "stale" existed
    monkeypatch.setattr(ssp, "LAST_RUN_FILE", last)
    plist = tmp_path / "list.md"
    plist.write_text("\n".join(_SECRET_URLS), encoding="utf-8")
    monkeypatch.setattr(ssp, "PLAYLIST_FILE", plist)
    sent: list[tuple[str, str]] = []

    def notify(title, body, _tags=""):
        sent.append((title, body))
        return True

    monkeypatch.setattr(ssp, "_notify", notify)
    u, other = _SECRET_URLS[2], _SECRET_URLS[0]
    ran = [other, u]
    ssp._alert_result(ssp.RunResult(stale=[u]), ran, ffmpeg_missing=False)
    ssp._alert_result(ssp.RunResult(stale=[u]), ran, ffmpeg_missing=False)
    assert sent == [("SC sync: success", f"No new tracks\n{_STALE_LINE}")]
    # still stale: listed in an alert sent for something else
    ssp._alert_result(ssp.RunResult(stale=[u], downloaded=1, new_tracks={other: 1}), ran, ffmpeg_missing=False)
    assert sent[-1] == ("SC sync: success", f"1 new track:\n- line 1: 1\n{_STALE_LINE}")
    # a run that skipped it doesn't forget it
    ssp._alert_result(ssp.RunResult(), [other], ffmpeg_missing=False)
    ssp._alert_result(ssp.RunResult(stale=[u]), ran, ffmpeg_missing=False)
    assert len(sent) == 2
    # fixed, then dead again: warned again
    ssp._alert_result(ssp.RunResult(), ran, ffmpeg_missing=False)
    ssp._alert_result(ssp.RunResult(stale=[u]), ran, ffmpeg_missing=False)
    assert len(sent) == 3


def test_finalize_stale_link_exits_0(playlist, tmp_path, monkeypatch):
    monkeypatch.setattr(ssp, "STATE_FILE", tmp_path / "state.json")
    assert ssp._finalize(ssp.RunResult(stale=[playlist]), unattended=True) == 0
    assert ssp._finalize(ssp.RunResult(stale=[playlist], needs_full=[playlist]), unattended=True) == 1


def _scripted_proc(lines: list[str], returncode: int):
    class Proc:
        def __init__(self, *_args, **_kwargs) -> None:
            self.stdout = iter(lines)
            self.returncode = returncode

        def wait(self) -> int:
            return returncode

    return Proc


_GEO = "ERROR: [soundcloud] This video is not available from your location due to geo restriction\n"
_SCDL_TRACK_ERRORS = [
    "[download] Downloading playlist: Metal Classics\n",
    "[download] Destination: a.m4a\n",
    _GEO,
    _GEO,
    "[download] Finished downloading playlist: Metal Classics\n",
]


def test_track_errors_in_a_synced_playlist_are_not_a_failure(playlist, monkeypatch):
    monkeypatch.setattr(ssp, "_fetch_ids_at", _fetch(["1", "2", "3"], ["6"]))
    monkeypatch.setattr(ssp.subprocess, "Popen", _scripted_proc(_SCDL_TRACK_ERRORS, 1))
    result = ssp.sync_list([playlist], 0, 5, force_all=False, unattended=True)
    assert result.failed == []
    assert result.track_errors == {playlist: 2}
    assert result.new_tracks == {playlist: 1}
    assert result.titles == {playlist: "Metal Classics"}


def test_playlist_that_did_not_finish_is_a_failure(playlist, monkeypatch):
    unfinished = [line for line in _SCDL_TRACK_ERRORS if "Finished" not in line]
    monkeypatch.setattr(ssp, "_fetch_ids_at", _fetch(["1", "2", "3"], ["6"]))
    monkeypatch.setattr(ssp.subprocess, "Popen", _scripted_proc(unfinished, 1))
    result = ssp.sync_list([playlist], 0, 5, force_all=False, unattended=True)
    assert result.failed == [playlist]
    assert result.track_errors == {}


def _snip(name: str) -> str:
    return f'[SnipDetection] Discarding "{name}" — 29.8s GO+ preview snip, not the full track\n'


_SCDL_SNIPS = [
    "[download] Downloading playlist: Metal\n",
    "[download] Destination: Metal\\a.mp3\n",
    "[download] Download completed\n",
    _snip("a.mp3"),
    "[download] Destination: Metal\\b.m4a\n",
    "[download] Download completed\n",
    "[download] Destination: Metal\\c.mp3\n",
    "[download] Download completed\n",
    _snip("c.mp3"),
    "[download] Finished downloading playlist: Metal\n",
]


def test_go_plus_previews_are_not_new_tracks(playlist, monkeypatch):
    """A preview that SnipDetectionPP deletes comes back every run: it must not be
    reported as a new track each day, but as a track that couldn't be downloaded
    (which alerts only when the set of such playlists changes)."""
    monkeypatch.setattr(ssp, "_fetch_ids_at", _fetch(["1", "2", "3"], ["6"]))
    monkeypatch.setattr(ssp.subprocess, "Popen", _scripted_proc(_SCDL_SNIPS, 0))
    result = ssp.sync_list([playlist], 0, 5, force_all=False, unattended=True)
    assert result.downloaded == 1
    assert result.new_tracks == {playlist: 1}
    assert result.track_errors == {playlist: 2}
    assert result.failed == []


def test_only_go_plus_previews_means_no_new_tracks(playlist, monkeypatch):
    only_snips = [line for line in _SCDL_SNIPS if "b.m4a" not in line]
    monkeypatch.setattr(ssp, "_fetch_ids_at", _fetch(["1", "2", "3"], ["6"]))
    monkeypatch.setattr(ssp.subprocess, "Popen", _scripted_proc(only_snips, 0))
    result = ssp.sync_list([playlist], 0, 5, force_all=False, unattended=True)
    assert result.downloaded == 0
    assert result.new_tracks == {}
    assert result.track_errors == {playlist: 2}


def test_go_plus_previews_add_to_track_errors(playlist, monkeypatch):
    lines = [*_SCDL_TRACK_ERRORS[:-1], _snip("a.m4a"), _SCDL_TRACK_ERRORS[-1]]
    monkeypatch.setattr(ssp, "_fetch_ids_at", _fetch(["1", "2", "3"], ["6"]))
    monkeypatch.setattr(ssp.subprocess, "Popen", _scripted_proc(lines, 1))
    result = ssp.sync_list([playlist], 0, 5, force_all=False, unattended=True)
    assert result.track_errors == {playlist: 3}
    assert result.new_tracks == {}


def test_finalize_track_errors_only_exits_0(playlist, monkeypatch, tmp_path):
    monkeypatch.setattr(ssp, "STATE_FILE", tmp_path / "state.json")
    result = ssp.RunResult(track_errors={playlist: 2}, titles={playlist: "Metal"})
    assert ssp._finalize(result, unattended=True) == 0
    assert ssp._finalize(ssp.RunResult(failed=[playlist]), unattended=True) == 1


def test_line_labels():
    assert ssp._fmt_ranges([10, 9, 1, 2, 3, 7]) == "1-3, 7, 9-10"
    line_of = {"a": 4}
    assert ssp._lines_label(["a"], line_of) == "line 4"
    assert ssp._lines_label(["a", "b", "c"], line_of) == "line 4 + 2 no longer in the list"


def test_repeat_failures_alert_once(tmp_path, monkeypatch, playlist):
    monkeypatch.setattr(ssp, "ytdlp_update_hint", lambda: None)
    monkeypatch.setattr(ssp, "LAST_RUN_FILE", tmp_path / "last.json")
    sent: list[str] = []

    def notify(_title, body, _tags=""):
        sent.append(body)
        return True

    monkeypatch.setattr(ssp, "_notify", notify)
    ran = [playlist]
    ssp._alert_result(ssp.RunResult(failed=[playlist]), ran, ffmpeg_missing=False)
    ssp._alert_result(ssp.RunResult(failed=[playlist]), ran, ffmpeg_missing=False)
    assert sent == ["No new tracks\nFailed: line 1 (see logs)"]
    ssp._alert_result(ssp.RunResult(), ran, ffmpeg_missing=False)
    ssp._alert_result(ssp.RunResult(), ran, ffmpeg_missing=False)
    assert sent[1:] == ["No new tracks\nEarlier failures cleared"]
    # a run that didn't include the failing playlist doesn't clear it
    ssp._alert_result(ssp.RunResult(failed=[playlist]), ran, ffmpeg_missing=False)
    ssp._alert_result(ssp.RunResult(), [], ffmpeg_missing=False)
    ssp._alert_result(ssp.RunResult(failed=[playlist]), ran, ffmpeg_missing=False)
    assert len(sent) == 3


def test_repeat_track_errors_alert_once(tmp_path, monkeypatch, playlist):
    monkeypatch.setattr(ssp, "ytdlp_update_hint", lambda: None)
    last = tmp_path / "last.json"
    last.write_text('{"failed": []}', encoding="utf-8")  # file from before track_errors existed
    monkeypatch.setattr(ssp, "LAST_RUN_FILE", last)
    sent: list[tuple[str, str]] = []

    def notify(title, body, _tags=""):
        sent.append((title, body))
        return True

    monkeypatch.setattr(ssp, "_notify", notify)
    ran = [playlist]

    def geo_blocked():
        return ssp.RunResult(track_errors={playlist: 2}, titles={playlist: "Metal"})

    ssp._alert_result(geo_blocked(), ran, ffmpeg_missing=False)
    ssp._alert_result(geo_blocked(), ran, ffmpeg_missing=False)
    assert sent == [("SC sync: success", "No new tracks\n2 tracks couldn't be downloaded (see logs):\n- Metal: 2")]
    # the same playlist now failing outright is a change
    ssp._alert_result(ssp.RunResult(failed=[playlist]), ran, ffmpeg_missing=False)
    assert sent[-1][0] == "SC sync: action needed"


def test_read_env(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "﻿# comment\n\nSC_NOTIFY_URL = \"https://ntfy.sh/abc\"\nexport OTHER='x y'\nBROKEN\n",
        encoding="utf-8",
    )
    assert ssp._read_env(env_file) == {"SC_NOTIFY_URL": "https://ntfy.sh/abc", "OTHER": "x y"}


def test_notify_without_url_sends_nothing(tmp_path, monkeypatch):
    monkeypatch.delenv("SC_NOTIFY_URL", raising=False)
    monkeypatch.setattr(ssp, "ENV_FILE", tmp_path / "missing.env")
    monkeypatch.setattr(ssp.urllib.request, "urlopen", _NoPopen)
    assert ssp._notify("SC sync", "body") is False


def test_notify_posts_without_printing_topic(monkeypatch, capsys):
    topic_url = "https://ntfy.sh/very-secret-topic"
    monkeypatch.setenv("SC_NOTIFY_URL", topic_url)
    requests = []

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    def urlopen(req, timeout):
        requests.append((req, timeout))
        return _Resp()

    monkeypatch.setattr(ssp.urllib.request, "urlopen", urlopen)
    assert ssp._notify("SC sync", "3 new tracks", "musical_note") is True
    req, _ = requests[0]
    assert req.full_url == topic_url
    assert req.data == b"3 new tracks"
    assert req.get_header("Title") == "SC sync"
    assert req.get_header("Tags") == "musical_note"
    assert "very-secret-topic" not in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Download lists (src/dl-audio.md / dl-video.md)
# ---------------------------------------------------------------------------

_YT = "https://www.youtube.com/watch?v=zq9secretvid"


def _queue(**kinds):
    return {k: ssp.download_queue.QueueResult(**v) for k, v in kinds.items()}


def test_alert_text_download_lists():
    result = ssp.RunResult(downloaded=1, queue=_queue(audio={"done": 2}, video={"done": 1, "failed": [_YT]}))
    title, body, tags = ssp._alert_text(result, {})
    assert (title, tags) == ("SC sync: success", "musical_note")
    assert body == (
        "1 new track\n"
        "Audio list: 2 downloaded\n"
        "Video list: 1 downloaded\n"
        "Video list: 1 link failed, kept for next run (see logs)"
    )
    assert "zq9secretvid" not in body
    assert "http" not in body
    _, body, _ = ssp._alert_text(ssp.RunResult(queue_crashed=True), {})
    assert body == "No new tracks\nDownload lists (dl-audio.md / dl-video.md) crashed (see logs)"


@pytest.fixture
def alerts(tmp_path, monkeypatch, playlist):
    monkeypatch.setattr(ssp, "ytdlp_update_hint", lambda: None)
    monkeypatch.setattr(ssp, "LAST_RUN_FILE", tmp_path / "last.json")
    sent: list[str] = []

    def notify(_title, body, _tags=""):
        sent.append(body)
        return True

    monkeypatch.setattr(ssp, "_notify", notify)
    return sent, [playlist]


def test_download_list_download_alerts(alerts):
    sent, ran = alerts
    ssp._alert_result(ssp.RunResult(queue=_queue(audio={"done": 1})), ran, ffmpeg_missing=False)
    ssp._alert_result(ssp.RunResult(queue=_queue(audio={"done": 1})), ran, ffmpeg_missing=False)
    assert sent == ["No new tracks\nAudio list: 1 downloaded"] * 2
    ssp._alert_result(ssp.RunResult(queue=_queue(audio={})), ran, ffmpeg_missing=False)
    assert len(sent) == 2  # nothing queued: silent


def test_download_list_failure_alerts_once(alerts):
    sent, ran = alerts
    failing = ssp.RunResult(queue=_queue(video={"failed": [_YT]}))
    ssp._alert_result(failing, ran, ffmpeg_missing=False)
    ssp._alert_result(failing, ran, ffmpeg_missing=False)
    assert sent == ["No new tracks\nVideo list: 1 link failed, kept for next run (see logs)"]
    # a crash says so, but doesn't forget the failing link
    ssp._alert_result(ssp.RunResult(queue_crashed=True), ran, ffmpeg_missing=False)
    ssp._alert_result(failing, ran, ffmpeg_missing=False)
    assert len(sent) == 2
    assert "crashed" in sent[1]
    assert "cleared" not in sent[1]
    # fixed (downloaded, or removed by hand)
    ssp._alert_result(ssp.RunResult(queue=_queue(video={})), ran, ffmpeg_missing=False)
    assert sent[-1] == "No new tracks\nEarlier failures cleared"


def test_run_queues_crash_is_contained(monkeypatch, capsys):
    def boom(_write):
        raise OSError("disk")

    monkeypatch.setattr(ssp.download_queue, "run_queues", boom)
    result = ssp.RunResult()
    ssp._run_queues(result)
    assert result.queue_crashed
    assert "OSError" in capsys.readouterr().err


def test_run_queues_records_results(monkeypatch):
    monkeypatch.setattr(ssp.download_queue, "run_queues", lambda _write: _queue(audio={"done": 3}))
    result = ssp.RunResult()
    ssp._run_queues(result)
    assert result.queue_done == 3
    assert not result.queue_crashed


def test_sync_module_does_not_import_ytdlp_at_load():
    """Scheduled runs upgrade yt-dlp before using it, so loading the module must not import it."""
    assert "ytdlp_age" not in vars(ssp)


def test_upgrade_ytdlp_without_uv_is_logged(monkeypatch, capsys):
    monkeypatch.setattr(ssp.shutil, "which", lambda *_a, **_k: None)
    monkeypatch.setattr(ssp, "_ytdlp_installed", lambda: "2026.01.01")
    ssp._upgrade_ytdlp()
    assert "not upgraded, uv not found" in capsys.readouterr().out


def test_upgrade_ytdlp_runs_uv_and_logs_new_version(monkeypatch, capsys):
    versions = iter(["2026.01.01", "2026.11.01"])
    calls: list[list[str]] = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        assert kwargs["cwd"] == ssp._ROOT
        return ssp.subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(ssp.shutil, "which", lambda *_a, **_k: "uv")
    monkeypatch.setattr(ssp, "_ytdlp_installed", lambda: next(versions))
    monkeypatch.setattr(ssp.subprocess, "run", run)
    ssp._upgrade_ytdlp()
    assert calls == [["uv", "sync", "--upgrade-package", "yt-dlp"]]
    assert "upgraded 2026.01.01 -> 2026.11.01" in capsys.readouterr().out


def test_upgrade_ytdlp_failure_does_not_raise(monkeypatch, capsys):
    monkeypatch.setattr(ssp.shutil, "which", lambda *_a, **_k: "uv")
    monkeypatch.setattr(ssp, "_ytdlp_installed", lambda: "2026.01.01")
    monkeypatch.setattr(ssp.subprocess, "run",
                        lambda cmd, **_k: ssp.subprocess.CompletedProcess(cmd, 2, "", "network error"))
    ssp._upgrade_ytdlp()
    out = capsys.readouterr().out
    assert "exited 2" in out
    assert "network error" in out


# ---------------------------------------------------------------------------
# YouTube retry of failed tracks (unattended runs)
# ---------------------------------------------------------------------------

def _failed_block(tag: str, n: int) -> str:
    return (f"{tag} someone - song {n}\n         https://soundcloud.com/someone/song-{n}\n"
            f"       → ERROR: [soundcloud] {100 + n}: This video is DRM protected\n\n")


def _fake_retry(failed_path: Path, keep: str, calls: list):
    """Fake retry_failed_with_ytdl.py run: leaves `keep` as the .failed content ('' deletes it)."""

    class Proc:
        def __init__(self, cmd, **_kwargs) -> None:
            calls.append(cmd)
            if keep:
                failed_path.write_text(keep, encoding="utf-8")
            else:
                failed_path.unlink()
            self.stdout = iter(["[1/2] someone - song 1 — searching YouTube\n",
                                "Recovered 1/2 track(s) via YouTube.\n"])
            self.returncode = 0

        def wait(self) -> int:
            return 0

    return Proc


def test_unattended_failed_tracks_are_tried_on_youtube(playlist, monkeypatch):
    failed = ssp.archive_path(playlist).with_suffix(".failed")
    monetize = _failed_block("[MONETIZE]", 3)
    failed.write_text(_failed_block("[FAIL]    ", 1) + _failed_block("[FAIL]    ", 2) + monetize, encoding="utf-8")
    calls: list = []
    monkeypatch.setattr(ssp.subprocess, "Popen", _fake_retry(failed, _failed_block("[FAIL]    ", 2) + monetize, calls))
    result = ssp.RunResult(track_errors={playlist: 3}, titles={playlist: "Metal"})
    ssp._recover_via_youtube(result, write=_quiet)
    assert calls[0][-1] == ssp.archive_path(playlist).stem
    assert result.yt_recovered == {playlist: 1}
    assert result.track_errors == {playlist: 2}  # one [FAIL] YouTube didn't find + the [MONETIZE] one

    # all recovered: the playlist no longer has failed tracks
    failed.write_text(_failed_block("[FAIL]    ", 1) + _failed_block("[FAIL]    ", 2), encoding="utf-8")
    monkeypatch.setattr(ssp.subprocess, "Popen", _fake_retry(failed, "", calls))
    result = ssp.RunResult(track_errors={playlist: 2})
    ssp._recover_via_youtube(result, write=_quiet)
    assert result.yt_recovered == {playlist: 2}
    assert result.track_errors == {}


def test_youtube_retry_skips_playlists_that_did_not_sync(playlist, monkeypatch):
    failed = ssp.archive_path(playlist).with_suffix(".failed")
    failed.write_text(_failed_block("[FAIL]    ", 1), encoding="utf-8")
    calls: list = []
    monkeypatch.setattr(ssp.subprocess, "Popen", _fake_retry(failed, "", calls))
    ssp._recover_via_youtube(ssp.RunResult(failed=[playlist]), write=_quiet)
    # only [GO+] / [MONETIZE] entries: nothing retry_failed_with_ytdl.py would act on
    failed.write_text(_failed_block("[GO+]     ", 1), encoding="utf-8")
    ssp._recover_via_youtube(ssp.RunResult(track_errors={playlist: 1}), write=_quiet)
    assert calls == []


def test_youtube_retry_crash_is_contained(monkeypatch, capsys):
    def boom(_result, _write=print):
        raise RuntimeError("boom")

    monkeypatch.setattr(ssp, "_recover_via_youtube", boom)
    ssp._run_youtube_recovery(ssp.RunResult())
    assert "RuntimeError" in capsys.readouterr().err


def test_alert_youtube_recovered_tracks(alerts):
    sent, ran = alerts
    result = ssp.RunResult(yt_recovered={ran[0]: 2}, titles={ran[0]: "Metal"})
    title, body, tags = ssp._alert_text(result, {ran[0]: 1})
    assert (title, tags) == ("SC sync: success", "musical_note")
    assert body == "No new tracks\n2 tracks downloaded from YouTube instead ([YT] files):\n- Metal: 2"
    ssp._alert_result(result, ran, ffmpeg_missing=False)
    assert sent == [body]
