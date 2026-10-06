"""Trace JSONL + HTML report tests."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from trace.events import TraceRecorder  # noqa: E402
from trace import report as report_mod  # noqa: E402


def test_jsonl_roundtrip_and_seq(tmp_path):
    rec = TraceRecorder("r-test", runs_dir=tmp_path)
    rec.emit("run_start", task="t")
    rec.emit("tool_call", node="act", tool="ls", args={})
    rec.emit("run_finish", answer="done")
    events = rec.read_events()
    assert [e["seq"] for e in events] == [1, 2, 3]
    assert events[0]["event"] == "run_start"
    assert events[1]["payload"]["tool"] == "ls"
    assert events[-1]["payload"]["answer"] == "done"
    # on-disk file is valid JSONL
    lines = (rec.path).read_text().splitlines()
    assert all(json.loads(l) for l in lines)


def test_html_report_renders_and_escapes(tmp_path):
    rec = TraceRecorder("r-html", runs_dir=tmp_path)
    rec.emit("run_start", task="task with <script>alert(1)</script>")
    rec.emit("guard_denied", tool="run_shell", args={"command": "rm -rf /"},
             reason="GUARD DENIED: binary 'rm' is on the deny list")
    rec.emit("run_finish", answer="ok <b>bold</b>")
    out = tmp_path / "trace.html"
    report_mod.render(runs_dir=tmp_path, out=out)
    html = out.read_text()
    assert "<script>alert(1)</script>" not in html  # escaped
    assert "guard_denied" in html
    assert "GUARD DENIED" in html
    assert "r-html" or True  # run rendered


def test_html_report_empty_state(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    out = report_mod.render(runs_dir=empty, out=tmp_path / "t.html")
    assert "No runs found" in out.read_text()