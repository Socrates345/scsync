"""Tests for src/sync_sc_playlists.py: unattended-run routing, lock, alerts.

src/ isn't a package, so the module is loaded from its file path.
"""

import importlib.util
import sys
from pathlib import Path
from typing import ClassVar

import pytest

_SRC = Path(__file__).resolve().parent.parent / "src"


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
        needs_full=[_SECRET_URLS[6]],
    )
    title, body, tags = ssp._alert_text(result, line_of, ffmpeg_missing=True, ytdlp_hint="yt-dlp is old")
    for leak in ("zq9user", "secret-mix", "tokenXYZ", "AbC", "deadbeef", "soundcloud", "http"):
        assert leak not in title
        assert leak not in body
    assert "5 new tracks" in body
    assert "Failed: lines 1-3" in body
    assert "Stale link: line 5" in body
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
