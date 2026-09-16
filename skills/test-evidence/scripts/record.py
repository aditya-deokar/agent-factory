#!/usr/bin/env python3
"""Screen recorder for evidence capture.

Four verbs:

    doctor    check that this machine can record, and say what is missing
    start     begin capturing; returns immediately
    annotate  timestamp a note against the running capture
    stop      finalize, burn the notes into the video, write a report

Raw capture goes to MPEG-TS, which has no central index and stays playable even
if the recorder is killed. The mp4 is produced on stop.

Session state lives in a directory (default .artifacts/evidence/<name>), so the
verbs are separate process invocations and nothing needs to stay resident.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_ROOT = Path(".artifacts") / "evidence"
IS_WINDOWS = os.name == "nt"


# --------------------------------------------------------------------------
# session state
# --------------------------------------------------------------------------

class Session:
    def __init__(self, directory: Path):
        self.dir = directory
        self.meta_path = directory / "session.json"
        self.notes_path = directory / "notes.jsonl"
        self.raw_path = directory / "raw.ts"
        self.video_path = directory / "evidence.mp4"

    @property
    def meta(self) -> dict:
        if not self.meta_path.exists():
            die(f"no session at {self.dir}. Run 'start' first.")
        return json.loads(self.meta_path.read_text(encoding="utf-8"))

    def write_meta(self, data: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self.meta_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def notes(self) -> list[dict]:
        if not self.notes_path.exists():
            return []
        out = []
        for line in self.notes_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                out.append(json.loads(line))
        return out

    def append_note(self, note: dict) -> None:
        with self.notes_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(note) + "\n")


def die(message: str, code: int = 1):
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(code)


# --------------------------------------------------------------------------
# capability probing
# --------------------------------------------------------------------------

def ffmpeg_bin() -> str | None:
    return shutil.which("ffmpeg")


def ffmpeg_supports(kind: str, name: str) -> bool:
    """kind is one of: encoders, filters, formats."""
    exe = ffmpeg_bin()
    if not exe:
        return False
    try:
        out = subprocess.run(
            [exe, "-hide_banner", f"-{kind}"],
            capture_output=True, text=True, timeout=30,
        ).stdout
    except (subprocess.SubprocessError, OSError):
        return False
    return any(line.split()[1:2] == [name] for line in out.splitlines() if line.strip())


def capture_input() -> tuple[list[str], str]:
    """Return (ffmpeg input args, human description) for this platform.

    EVIDENCE_TEST_INPUT overrides with a raw argument string, which lets the
    test suite drive a synthetic source with no display attached.
    """
    override = os.environ.get("EVIDENCE_TEST_INPUT")
    if override:
        return override.split(), f"override: {override}"

    system = platform.system()

    if system == "Linux":
        display = os.environ.get("DISPLAY")
        if display:
            size = os.environ.get("EVIDENCE_SIZE", "1920x1080")
            return (
                ["-f", "x11grab", "-framerate", "15", "-video_size", size, "-i", display],
                f"x11grab on {display}",
            )
        if os.environ.get("WAYLAND_DISPLAY"):
            return [], "wayland (use wf-recorder; ffmpeg cannot capture wlroots directly)"
        return [], "no DISPLAY and no WAYLAND_DISPLAY"

    if system == "Darwin":
        index = os.environ.get("EVIDENCE_AVF_INDEX", "1")
        return (
            ["-f", "avfoundation", "-framerate", "15", "-i", f"{index}:none"],
            f"avfoundation device {index} (needs Screen Recording permission)",
        )

    if system == "Windows":
        return (
            ["-f", "gdigrab", "-framerate", "15", "-i", "desktop"],
            "gdigrab on the desktop",
        )

    return [], f"unsupported platform: {system}"


def cmd_doctor(args) -> int:
    checks: list[tuple[str, bool, str]] = []

    exe = ffmpeg_bin()
    checks.append(("ffmpeg", bool(exe), exe or "not on PATH"))
    probe = shutil.which("ffprobe")
    checks.append(("ffprobe", bool(probe), probe or "not on PATH"))

    if exe:
        h264 = ffmpeg_supports("encoders", "libx264")
        checks.append(("libx264 encoder", h264, "present" if h264 else "rebuild ffmpeg with --enable-libx264"))
        ass = ffmpeg_supports("filters", "ass")
        checks.append(("ass filter", ass, "present" if ass else "rebuild ffmpeg with --enable-libass"))

    inp, description = capture_input()
    checks.append(("capture source", bool(inp), description))

    width = max(len(name) for name, _, _ in checks)
    for name, ok, detail in checks:
        mark = "ok  " if ok else "FAIL"
        print(f"  {mark}  {name.ljust(width)}  {detail}")

    failed = [name for name, ok, _ in checks if not ok]
    if failed:
        print(f"\n{len(failed)} check(s) failed: {', '.join(failed)}")
        print("The headless path (scripted screenshots) still works without these.")
        return 1
    print("\nReady to record.")
    return 0


# --------------------------------------------------------------------------
# start / annotate / stop
# --------------------------------------------------------------------------

def cmd_start(args) -> int:
    session = Session(Path(args.dir))
    if session.meta_path.exists():
        meta = session.meta
        if meta.get("state") == "recording":
            die(f"already recording (pid {meta.get('pid')}). Stop it first.")

    exe = ffmpeg_bin()
    if not exe:
        die("ffmpeg is not on PATH. Run 'doctor' for detail.")

    inp, description = capture_input()
    if not inp:
        die(f"no usable capture source: {description}")

    session.dir.mkdir(parents=True, exist_ok=True)
    if session.notes_path.exists():
        session.notes_path.unlink()

    cmd = [
        exe, "-hide_banner", "-loglevel", "warning", "-y",
        *inp,
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-g", "30",
        "-f", "mpegts", str(session.raw_path),
    ]

    # A new process group on Windows is what makes CTRL_BREAK deliverable later.
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if IS_WINDOWS else 0
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=(session.dir / "ffmpeg.log").open("wb"),
        creationflags=creationflags,
        start_new_session=not IS_WINDOWS,
    )

    time.sleep(1.5)
    if proc.poll() is not None:
        log = (session.dir / "ffmpeg.log").read_text(encoding="utf-8", errors="replace")
        die(f"ffmpeg exited immediately.\n{log.strip()}")

    session.write_meta({
        "state": "recording",
        "pid": proc.pid,
        "started_at": time.time(),
        "source": description,
        "label": args.label,
        "command": cmd,
    })

    print(f"recording -> {session.raw_path}")
    print(f"source: {description}")
    print(f"annotate with: python {sys.argv[0]} annotate \"<note>\" --dir {session.dir}")
    return 0


def cmd_annotate(args) -> int:
    session = Session(Path(args.dir))
    meta = session.meta
    if meta.get("state") != "recording":
        die("session is not recording.")

    offset = time.time() - meta["started_at"]
    note = {"t": round(offset, 2), "kind": args.kind, "text": args.text}
    session.append_note(note)
    print(f"[{fmt_clock(offset)}] {args.kind}: {args.text}")
    return 0


def cmd_stop(args) -> int:
    session = Session(Path(args.dir))
    meta = session.meta
    if meta.get("state") != "recording":
        die("session is not recording.")

    stop_process(meta["pid"])
    duration = time.time() - meta["started_at"]

    if not session.raw_path.exists() or session.raw_path.stat().st_size == 0:
        die("capture produced no data. Check ffmpeg.log in the session directory.")

    notes = session.notes()
    subtitle = session.dir / "notes.ass" if notes else None
    if subtitle:
        subtitle.write_text(build_ass(notes, duration), encoding="utf-8")

    encode(session, subtitle)

    meta.update({
        "state": "stopped",
        "stopped_at": time.time(),
        "duration": round(duration, 2),
        "note_count": len(notes),
    })
    session.write_meta(meta)

    (session.dir / "report.md").write_text(build_report(meta, notes), encoding="utf-8")
    (session.dir / "manifest.json").write_text(
        json.dumps({"session": meta, "notes": notes}, indent=2), encoding="utf-8"
    )

    if not args.keep_raw:
        session.raw_path.unlink(missing_ok=True)

    print(f"video:    {session.video_path}")
    print(f"report:   {session.dir / 'report.md'}")
    print(f"duration: {fmt_clock(duration)}, {len(notes)} annotation(s)")
    return 0


def stop_process(pid: int) -> None:
    """Ask ffmpeg to finish, then insist. MPEG-TS survives either outcome."""
    sig = signal.CTRL_BREAK_EVENT if IS_WINDOWS else signal.SIGINT
    try:
        os.kill(pid, sig)
    except (ProcessLookupError, OSError):
        return  # already gone; the TS on disk is still usable

    for _ in range(50):  # up to 5s for a clean flush
        time.sleep(0.1)
        try:
            os.kill(pid, 0)
        except OSError:
            return
    try:
        os.kill(pid, signal.SIGKILL if not IS_WINDOWS else signal.SIGTERM)
    except OSError:
        pass


def encode(session: Session, subtitle: Path | None) -> None:
    cmd = [ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y", "-i", str(session.raw_path)]
    if subtitle:
        # Escape for the filter parser: backslashes and the colon in C:\...
        escaped = str(subtitle).replace("\\", "/").replace(":", "\\:")
        cmd += ["-vf", f"ass='{escaped}'", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23"]
    else:
        cmd += ["-c", "copy"]
    cmd += ["-movflags", "+faststart", str(session.video_path)]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        die(f"encode failed:\n{result.stderr.strip()}")


# --------------------------------------------------------------------------
# output formatting
# --------------------------------------------------------------------------

KIND_COLOUR = {
    "pass": "&H0000FF00",   # ASS is &HAABBGGRR
    "fail": "&H000000FF",
    "step": "&H00FFFFFF",
    "note": "&H0000D7FF",
}


def fmt_clock(seconds: float) -> str:
    return f"{int(seconds // 60):02d}:{int(seconds % 60):02d}"


def ass_time(seconds: float) -> str:
    h, rem = divmod(max(seconds, 0), 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def build_ass(notes: list[dict], duration: float, hold: float = 4.0) -> str:
    head = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        "PlayResX: 1920\n"
        "PlayResY: 1080\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour,"
        " Bold, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
    )
    for kind, colour in KIND_COLOUR.items():
        head += (
            f"Style: {kind},Arial,44,{colour},&H00000000,&H96000000,"
            "-1,3,2,1,1,60,60,50,1\n"
        )
    head += "\n[Events]\nFormat: Layer, Start, End, Style, Text\n"

    lines = []
    for note in notes:
        start = note["t"]
        end = min(start + hold, duration + hold)
        kind = note["kind"] if note["kind"] in KIND_COLOUR else "note"
        text = str(note["text"]).replace("\n", " ").replace("{", "(").replace("}", ")")
        lines.append(f"Dialogue: 0,{ass_time(start)},{ass_time(end)},{kind},{kind.upper()}: {text}")
    return head + "\n".join(lines) + "\n"


def build_report(meta: dict, notes: list[dict]) -> str:
    counts: dict[str, int] = {}
    for note in notes:
        counts[note["kind"]] = counts.get(note["kind"], 0) + 1

    out = ["# Evidence report", ""]
    if meta.get("label"):
        out += [f"**{meta['label']}**", ""]
    out += [
        f"- Duration: {fmt_clock(meta.get('duration', 0))}",
        f"- Source: {meta.get('source', 'unknown')}",
        f"- Annotations: {len(notes)}"
        + (f" ({', '.join(f'{v} {k}' for k, v in sorted(counts.items()))})" if counts else ""),
        "",
    ]

    failures = [n for n in notes if n["kind"] == "fail"]
    if failures:
        out += ["## Failures", ""]
        out += [f"- `{fmt_clock(n['t'])}` {n['text']}" for n in failures]
        out += [""]

    out += ["## Timeline", "", "| Time | Kind | Note |", "|---|---|---|"]
    for note in notes:
        text = str(note["text"]).replace("|", "\\|")
        out.append(f"| {fmt_clock(note['t'])} | {note['kind']} | {text} |")
    out += ["", "Video: `evidence.mp4`. Annotations are burned in at the times above."]
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="record.py", description=__doc__)
    parser.add_argument("--dir", default=str(DEFAULT_ROOT / "session"),
                        help="session directory (default: .artifacts/evidence/session)")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="check recording prerequisites").set_defaults(fn=cmd_doctor)

    start = sub.add_parser("start", help="begin capturing")
    start.add_argument("--label", default="", help="what this session is proving")
    start.set_defaults(fn=cmd_start)

    annotate = sub.add_parser("annotate", help="timestamp a note")
    annotate.add_argument("text")
    annotate.add_argument("--kind", default="step", choices=sorted(KIND_COLOUR),
                          help="step (default), pass, fail, note")
    annotate.set_defaults(fn=cmd_annotate)

    stop = sub.add_parser("stop", help="finalize and render")
    stop.add_argument("--keep-raw", action="store_true", help="keep raw.ts after encoding")
    stop.set_defaults(fn=cmd_stop)

    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
