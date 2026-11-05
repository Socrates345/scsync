"""Tests for src/download_queue.py: link parsing, link removal, the download loop.

src/ isn't a package, so the module is loaded from its file path.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parent.parent / "src"


def _load():
    if str(_SRC) not in sys.path:
        sys.path.insert(0, str(_SRC))
    spec = importlib.util.spec_from_file_location("download_queue", _SRC / "download_queue.py")
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["download_queue"] = mod  # dataclasses with string annotations look the module up
    spec.loader.exec_module(mod)
    return mod


dq = _load()

A = "https://www.youtube.com/watch?v=aaaaaaaaaaa"
B = "https://youtu.be/bbbbbbbbbbb?si=xyz"
C = "https://soundcloud.com/someone/a-track"


def _quiet(_line: str) -> None:
    pass


def _write(path: Path, text: str) -> None:
    path.write_bytes(text.encode("utf-8"))


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def test_read_links_formats(tmp_path):
    f = tmp_path / "dl.md"
    _write(f, "﻿" + A + "\r\n"
           "# Heading with https://example.com/not-a-link\r\n"
           "some note about https://example.com/also-not\r\n"
           f"- {B}\r\n"
           f"  <{C}>  \r\n"
           f"1. {A}\r\n"  # duplicate
           "[title](https://example.com/md-link)\r\n"
           "\r\n")
    assert dq.read_links(f) == [A, B, C]


def test_read_links_missing_file(tmp_path):
    assert dq.read_links(tmp_path / "nope.md") == []


# ---------------------------------------------------------------------------
# Removal
# ---------------------------------------------------------------------------


def test_remove_link_keeps_everything_else(tmp_path):
    f = tmp_path / "dl.md"
    _write(f, f"# header\r\n{A}\r\nnote\r\n- {A}\r\n{B}")
    assert dq.remove_link(f, A, _quiet)
    assert f.read_bytes() == f"# header\r\nnote\r\n{B}".encode()
    assert not (tmp_path / ".dl.md.tmp").exists()


def test_remove_link_keeps_bom_and_prefix_matches(tmp_path):
    f = tmp_path / "dl.md"
    _write(f, f"﻿{A}\n{A}&t=10\n")
    assert dq.remove_link(f, A, _quiet)
    assert f.read_bytes() == f"﻿{A}&t=10\n".encode()


def test_remove_link_already_gone(tmp_path):
    f = tmp_path / "dl.md"
    _write(f, f"{B}\n")
    assert dq.remove_link(f, A, _quiet)
    assert f.read_text(encoding="utf-8") == f"{B}\n"
    assert dq.remove_link(tmp_path / "missing.md", A, _quiet)


def test_remove_link_gives_up_when_locked(tmp_path, monkeypatch):
    f = tmp_path / "dl.md"
    _write(f, f"{A}\n")

    def locked(*_args):
        raise PermissionError

    monkeypatch.setattr(dq.os, "replace", locked)
    monkeypatch.setattr(dq.time, "sleep", lambda _s: None)
    lines: list[str] = []
    assert not dq.remove_link(f, A, lines.append)
    assert f.read_text(encoding="utf-8") == f"{A}\n"
    assert not (tmp_path / ".dl.md.tmp").exists()
    assert "couldn't update" in lines[0]


# ---------------------------------------------------------------------------
# Download loop
# ---------------------------------------------------------------------------


@pytest.fixture
def runs(monkeypatch):
    """Fake ytdl.py: outcome per link from the dict (default success); records calls."""
    outcomes: dict[str, bool] = {}
    calls: list[tuple[str, bool]] = []

    def fake(url, video, _write=print):
        calls.append((url, video))
        return outcomes.get(url, True)

    monkeypatch.setattr(dq, "_run_ytdl", fake)
    return outcomes, calls


def test_success_removes_failure_keeps(tmp_path, runs):
    outcomes, calls = runs
    f = tmp_path / "dl.md"
    _write(f, f"# list\n{A}\n{B}\n{C}\n")
    outcomes[B] = False
    res = dq.process_list(f, video=True, write=_quiet, delay=0)
    assert (res.done, res.failed, res.not_tried) == (2, [B], 0)
    assert calls == [(A, True), (B, True), (C, True)]
    assert f.read_text(encoding="utf-8") == f"# list\n{B}\n"


def test_links_added_during_the_run_survive(tmp_path, monkeypatch):
    f = tmp_path / "dl.md"
    _write(f, f"{A}\n")

    def fake(_url, _video, _write=print):
        with open(f, "a", encoding="utf-8") as fh:  # user adds a link mid-download
            fh.write(f"{B}\n")
        return True

    monkeypatch.setattr(dq, "_run_ytdl", fake)
    res = dq.process_list(f, video=False, write=_quiet, delay=0)
    assert res.done == 1
    assert f.read_text(encoding="utf-8") == f"{B}\n"


def test_stops_after_failures_in_a_row(tmp_path, runs):
    outcomes, calls = runs
    links = [f"https://youtu.be/v{i}" for i in range(6)]
    f = tmp_path / "dl.md"
    _write(f, "\n".join(links) + "\n")
    for u in links:
        outcomes[u] = False
    res = dq.process_list(f, video=False, write=_quiet, delay=0)
    assert res.failed == links[:3]
    assert res.not_tried == 3
    assert len(calls) == 3
    assert dq.read_links(f) == links


def test_success_resets_failure_streak(tmp_path, runs):
    outcomes, calls = runs
    links = [f"https://youtu.be/v{i}" for i in range(5)]
    f = tmp_path / "dl.md"
    _write(f, "\n".join(links) + "\n")
    for u in (links[0], links[1], links[3], links[4]):
        outcomes[u] = False
    res = dq.process_list(f, video=False, write=_quiet, delay=0)
    assert len(calls) == 5
    assert (res.done, res.not_tried) == (1, 0)


def test_last_link_failing_is_not_a_stop(tmp_path, runs):
    outcomes, _ = runs
    links = [f"https://youtu.be/v{i}" for i in range(3)]
    f = tmp_path / "dl.md"
    _write(f, "\n".join(links) + "\n")
    for u in links:
        outcomes[u] = False
    assert dq.process_list(f, video=False, write=_quiet, delay=0).not_tried == 0


def test_missing_or_empty_list_does_nothing(tmp_path, runs):
    _, calls = runs
    f = tmp_path / "dl.md"
    _write(f, "# nothing here\n")
    assert dq.process_list(f, video=False, write=_quiet, delay=0) == dq.QueueResult()
    assert dq.process_list(tmp_path / "missing.md", video=False, write=_quiet, delay=0) == dq.QueueResult()
    assert calls == []


def test_run_queues_audio_then_video(tmp_path, monkeypatch, runs):
    _, calls = runs
    audio, video = tmp_path / "a.md", tmp_path / "v.md"
    _write(audio, f"{C}\n")
    _write(video, f"{A}\n")
    monkeypatch.setattr(dq, "QUEUES", (("audio", audio, False), ("video", video, True)))
    monkeypatch.setattr(dq, "DELAY", 0)
    res = dq.run_queues(_quiet)
    assert calls == [(C, False), (A, True)]
    assert res["audio"].done == res["video"].done == 1


# ---------------------------------------------------------------------------
# ytdl.py subprocess
# ---------------------------------------------------------------------------


def _fake_popen(monkeypatch, lines: list[str], returncode: int) -> list[list[str]]:
    cmds: list[list[str]] = []

    class Proc:
        def __init__(self, cmd, **_kwargs) -> None:
            cmds.append(cmd)
            self.stdout = iter(lines)
            self.returncode = returncode

        def wait(self) -> int:
            return self.returncode

    monkeypatch.setattr(dq.subprocess, "Popen", Proc)
    return cmds


def test_run_ytdl_command_and_success(monkeypatch):
    cmds = _fake_popen(monkeypatch, ["[download] Destination: x.mp4\n"], 0)
    assert dq._run_ytdl(A, True, _quiet)
    assert cmds[0][1:] == [str(dq.YTDL_SCRIPT), "--video", "-l", A]
    assert dq._run_ytdl(C, False, _quiet)
    assert cmds[1][1:] == [str(dq.YTDL_SCRIPT), "-l", C]


@pytest.mark.parametrize(("lines", "code"), [
    (["ERROR: [youtube] x: Video unavailable\n"], 1),
    (["ERROR: something\n"], 0),
    ([], 2),
])
def test_run_ytdl_failure(monkeypatch, lines, code):
    _fake_popen(monkeypatch, lines, code)
    assert not dq._run_ytdl(A, False, _quiet)


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


def test_summary_lines_have_no_links():
    res = {
        "audio": dq.QueueResult(done=2),
        "video": dq.QueueResult(done=1, failed=[A, B, C], not_tried=4),
    }
    assert dq.summary_lines(res) == [
        "Audio list: 2 downloaded",
        "Video list: 1 downloaded",
        "Video list: 3 links failed, kept for next run, 4 not tried (see logs)",
    ]
    assert dq.summary_lines({"audio": dq.QueueResult(failed=[A])}) == [
        "Audio list: 1 link failed, kept for next run (see logs)",
    ]
    assert dq.summary_lines({"audio": dq.QueueResult(), "video": dq.QueueResult()}) == []
