"""Trace events: JSONL on disk + streamed to stderr as they happen."""

from __future__ import annotations

import json
import sys
import time
import uuid
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def new_run_id() -> str:
    return time.strftime("r-%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]


class TraceRecorder:
    """Writes one JSONL event per line; mirrors every event to stderr."""

    def __init__(self, run_id: str, runs_dir: Path | None = None):
        self.run_id = run_id
        self.runs_dir = Path(runs_dir) if runs_dir else PROJECT_ROOT / "runs"
        self.run_dir = self.runs_dir / run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.run_dir / "trace.jsonl"
        self._seq = 0
        self._t0 = time.monotonic()

    def emit(self, event: str, node: str = "", **payload) -> dict:
        self._seq += 1
        rec = {
            "seq": self._seq,
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
                  + f".{int(time.time() * 1000) % 1000:03d}Z",
            "elapsed_ms": int((time.monotonic() - self._t0) * 1000),
            "run_id": self.run_id,
            "event": event,
            "node": node,
            "payload": payload,
        }
        line = json.dumps(rec, ensure_ascii=False)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        print(f"[trace {self.run_id}] #{rec['seq']} {event}" +
              (f" ({node})" if node else "") +
              (f" :: {json.dumps(payload, ensure_ascii=False)[:220]}"
               if payload else ""), file=sys.stderr, flush=True)
        return rec

    def read_events(self) -> list[dict]:
        events = []
        with self.path.open(encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    events.append(json.loads(line))
        return events