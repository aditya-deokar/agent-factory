"""Tests for the evidence recorder.

The pure logic (session state, annotation timing, ASS generation, reports) runs
everywhere. The capture and encode path needs ffmpeg and is skipped without it;
CI installs ffmpeg so that path is covered there.

    python -m pytest tests/ -q
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "evidence-driven-testing" / "scripts" / "record.py"

spec = importlib.util.spec_from_file_location("record", SCRIPT)
record = importlib.util.module_from_spec(spec)
sys.modules["record"] = record
spec.loader.exec_module(record)

has_ffmpeg = shutil.which("ffmpeg") is not None
needs_ffmpeg = pytest.mark.skipif(not has_ffmpeg, reason="ffmpeg not installed")


# --------------------------------------------------------------------------
# formatting
# --------------------------------------------------------------------------

@pytest.mark.parametrize("seconds,expected", [
    (0, "00:00"),
    (9.4, "00:09"),
    (61, "01:01"),
    (600, "10:00"),
    (3661, "61:01"),
])
def test_fmt_clock(seconds, expected):
    assert record.fmt_clock(seconds) == expected


@pytest.mark.parametrize("seconds,expected", [
    (0, "0:00:00.00"),
    (5.25, "0:00:05.25"),
    (90, "0:01:30.00"),
    (3725.5, "1:02:05.50"),
])
def test_ass_time(seconds, expected):
    assert record.ass_time(seconds) == expected


def test_ass_time_clamps_negative():
    # A note can land microscopically before zero from clock jitter.
    assert record.ass_time(-1) == "0:00:00.00"


# --------------------------------------------------------------------------
# subtitle generation
# --------------------------------------------------------------------------

def test_build_ass_has_a_style_per_kind():
    out = record.build_ass([], 10)
    for kind in record.KIND_COLOUR:
        assert f"Style: {kind}," in out


def test_build_ass_emits_one_dialogue_per_note():
    notes = [
        {"t": 1.0, "kind": "step", "text": "opened the page"},
        {"t": 4.5, "kind": "pass", "text": "banner renders"},
        {"t": 9.0, "kind": "fail", "text": "count is stale"},
    ]
    lines = [l for l in record.build_ass(notes, 20).splitlines() if l.startswith("Dialogue:")]
    assert len(lines) == 3
    assert "STEP: opened the page" in lines[0]
    assert "PASS: banner renders" in lines[1]
    assert "FAIL: count is stale" in lines[2]
    assert "0:00:01.00" in lines[0]


def test_build_ass_escapes_override_braces():
    # Braces are ASS override blocks; unescaped they swallow the text.
    out = record.build_ass([{"t": 0, "kind": "note", "text": "use {bold} here"}], 5)
    assert "{bold}" not in out
    assert "(bold)" in out


def test_build_ass_flattens_newlines():
    out = record.build_ass([{"t": 0, "kind": "note", "text": "line one\nline two"}], 5)
    dialogue = [l for l in out.splitlines() if l.startswith("Dialogue:")][0]
    assert "line one line two" in dialogue


def test_build_ass_falls_back_for_unknown_kind():
    out = record.build_ass([{"t": 0, "kind": "bogus", "text": "x"}], 5)
    assert "Dialogue: 0,0:00:00.00,0:00:04.00,note," in out


# --------------------------------------------------------------------------
# report
# --------------------------------------------------------------------------

def test_report_surfaces_failures_in_their_own_section():
    meta = {"duration": 30, "source": "test", "label": "Checkout flow"}
    notes = [
        {"t": 2, "kind": "pass", "text": "cart loads"},
        {"t": 11, "kind": "fail", "text": "tax is wrong for NY"},
    ]
    out = record.build_report(meta, notes)
    assert "Checkout flow" in out
    assert "## Failures" in out
    assert "tax is wrong for NY" in out
    assert "`00:11`" in out


def test_report_omits_failure_section_when_all_passed():
    out = record.build_report({"duration": 5, "source": "t"}, [{"t": 1, "kind": "pass", "text": "ok"}])
    assert "## Failures" not in out
    assert "## Timeline" in out


def test_report_escapes_table_pipes():
    out = record.build_report({"duration": 5, "source": "t"},
                              [{"t": 1, "kind": "note", "text": "a | b"}])
    assert r"a \| b" in out


def test_report_counts_by_kind():
    notes = [{"t": 1, "kind": "pass", "text": "x"}, {"t": 2, "kind": "pass", "text": "y"},
             {"t": 3, "kind": "fail", "text": "z"}]
    out = record.build_report({"duration": 5, "source": "t"}, notes)
    assert "1 fail, 2 pass" in out


# --------------------------------------------------------------------------
# session state
# --------------------------------------------------------------------------

def test_session_reads_back_what_it_wrote(tmp_path):
    session = record.Session(tmp_path / "s")
    session.write_meta({"state": "recording", "pid": 1})
    assert session.meta["state"] == "recording"


def test_missing_session_exits(tmp_path):
    with pytest.raises(SystemExit):
        _ = record.Session(tmp_path / "nope").meta


def test_notes_round_trip_as_jsonl(tmp_path):
    session = record.Session(tmp_path / "s")
    session.dir.mkdir(parents=True)
    session.append_note({"t": 1.0, "kind": "step", "text": "first"})
    session.append_note({"t": 2.0, "kind": "pass", "text": "second"})
    notes = session.notes()
    assert [n["text"] for n in notes] == ["first", "second"]


def test_notes_empty_before_any_written(tmp_path):
    assert record.Session(tmp_path / "s").notes() == []


def test_annotate_offsets_from_session_start(tmp_path, capsys):
    session = record.Session(tmp_path / "s")
    session.write_meta({"state": "recording", "pid": 1, "started_at": time.time() - 7.0})

    args = record.main(["--dir", str(session.dir), "annotate", "checked the total", "--kind", "pass"])
    assert args == 0

    note = session.notes()[0]
    assert note["kind"] == "pass"
    assert 6.5 < note["t"] < 8.0          # ~7s, allowing for scheduling
    assert "00:07" in capsys.readouterr().out


def test_annotate_refuses_when_not_recording(tmp_path):
    session = record.Session(tmp_path / "s")
    session.write_meta({"state": "stopped", "started_at": time.time()})
    with pytest.raises(SystemExit):
        record.main(["--dir", str(session.dir), "annotate", "x"])


def test_capture_input_honours_test_override(monkeypatch):
    monkeypatch.setenv("EVIDENCE_TEST_INPUT", "-f lavfi -i testsrc2=size=320x240:rate=10")
    args, description = record.capture_input()
    assert args == ["-f", "lavfi", "-i", "testsrc2=size=320x240:rate=10"]
    assert "override" in description


# --------------------------------------------------------------------------
# full capture path
# --------------------------------------------------------------------------

@needs_ffmpeg
def test_doctor_reports_ffmpeg(monkeypatch, capsys):
    monkeypatch.setenv("EVIDENCE_TEST_INPUT", "-f lavfi -i testsrc2=size=320x240:rate=10")
    record.main(["doctor"])
    assert "ffmpeg" in capsys.readouterr().out


@needs_ffmpeg
def test_record_start_annotate_stop(tmp_path, monkeypatch):
    """The whole loop against a synthetic source, so it runs with no display."""
    monkeypatch.setenv("EVIDENCE_TEST_INPUT", "-f lavfi -i testsrc2=size=320x240:rate=10")
    directory = tmp_path / "session"

    assert record.main(["--dir", str(directory), "start", "--label", "Smoke"]) == 0
    session = record.Session(directory)
    assert session.meta["state"] == "recording"

    record.main(["--dir", str(directory), "annotate", "first step"])
    time.sleep(1.0)
    record.main(["--dir", str(directory), "annotate", "it works", "--kind", "pass"])
    time.sleep(0.5)

    assert record.main(["--dir", str(directory), "stop"]) == 0

    assert session.video_path.exists()
    assert session.video_path.stat().st_size > 0
    assert not session.raw_path.exists()          # cleaned up by default

    meta = session.meta
    assert meta["state"] == "stopped"
    assert meta["note_count"] == 2
    assert meta["duration"] > 1.0

    report = (directory / "report.md").read_text(encoding="utf-8")
    assert "Smoke" in report and "it works" in report

    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    assert len(manifest["notes"]) == 2


@needs_ffmpeg
def test_start_refuses_to_double_start(tmp_path, monkeypatch):
    monkeypatch.setenv("EVIDENCE_TEST_INPUT", "-f lavfi -i testsrc2=size=320x240:rate=10")
    directory = tmp_path / "session"
    record.main(["--dir", str(directory), "start"])
    try:
        with pytest.raises(SystemExit):
            record.main(["--dir", str(directory), "start"])
    finally:
        record.main(["--dir", str(directory), "stop"])


@needs_ffmpeg
def test_keep_raw_leaves_the_ts(tmp_path, monkeypatch):
    monkeypatch.setenv("EVIDENCE_TEST_INPUT", "-f lavfi -i testsrc2=size=320x240:rate=10")
    directory = tmp_path / "session"
    record.main(["--dir", str(directory), "start"])
    time.sleep(1.0)
    record.main(["--dir", str(directory), "stop", "--keep-raw"])
    assert record.Session(directory).raw_path.exists()
