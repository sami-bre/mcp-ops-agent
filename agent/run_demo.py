"""Demo runner: scenarios -> agent runs -> trace.jsonl files -> trace.html.

Mock mode (default): deterministic MockLLM scripts, zero API spend.
Live mode:  MCP_OPS_MODE=live OPENROUTER_API_KEY=sk-... python -m agent.run_demo
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from agent.llm import MockLLM, OpenRouterLLM
from agent.mcp_client import open_mcp_tools
from agent.graph import run_task
from trace.events import TraceRecorder, new_run_id
from trace import report

SCENARIOS = PROJECT_ROOT / "eval" / "scenarios.jsonl"
RUNS_DIR = PROJECT_ROOT / "runs"


def load_scenarios() -> list[dict]:
    return [json.loads(l) for l in SCENARIOS.read_text().splitlines() if l.strip()]


def make_providers(scenario: dict) -> list[tuple[str, object]]:
    """One provider per scenario. Live mode -> real OpenRouter; else MockLLM."""
    if os.environ.get("MCP_OPS_MODE", "mock") == "live":
        prov = OpenRouterLLM()
        return [(prov.name, prov)]
    return [("mock", MockLLM(scenario["mock_script"]))]


async def main() -> int:
    scenarios = load_scenarios()
    # fresh runs dir each demo so the report reflects exactly this run
    if RUNS_DIR.exists():
        shutil.rmtree(RUNS_DIR)
    RUNS_DIR.mkdir(parents=True, exist_ok=True)

    results = []
    spend_notes = []
    async with open_mcp_tools() as tools:
        print(f"[demo] MCP server up: {len(tools.tool_schemas)} tools: "
              + ", ".join(t["name"] for t in tools.tool_schemas), flush=True)
        for sc in scenarios:
            for prov_name, provider in make_providers(sc):
                run_id = new_run_id()
                print(f"\n=== scenario '{sc['name']}' provider={prov_name} run={run_id} ===",
                      file=sys.stderr, flush=True)
                runs_path = RUNS_DIR / run_id
                runs_path.mkdir()
                recorder = TraceRecorder(run_id, runs_dir=RUNS_DIR)
                t0 = time.monotonic()
                final = await run_task(sc["task"], provider, tools, recorder)
                dur = time.monotonic() - t0
                events = recorder.read_events()
                tools_used = [e["payload"]["tool"] for e in events if e["event"] == "tool_call"]
                denied = sum(1 for e in events if e["event"] == "guard_denied")
                ok = True
                expect = sc.get("expect", {})
                if prov_name == "mock":
                    if tools_used != expect.get("tools", tools_used):
                        ok = False
                        print(f"[check] FAIL {sc['name']}: tools {tools_used} != {expect.get('tools')}")
                    if denied != expect.get("guard_denied", 0):
                        ok = False
                        print(f"[check] FAIL {sc['name']}: guard_denied {denied} != {expect.get('guard_denied', 0)}")
                    for frag in expect.get("answer_contains", []):
                        if frag.lower() not in (final.get("answer") or "").lower():
                            ok = False
                            print(f"[check] FAIL {sc['name']}: answer missing {frag!r}")
                status = "PASS" if ok else "FAIL"
                print(f"[check] {status} {sc['name']} ({prov_name}): steps={final.get('steps')} "
                      f"denied={denied} duration={dur:.1f}s", file=sys.stderr, flush=True)
                if isinstance(provider, OpenRouterLLM):
                    u = provider.usage
                    spend_notes.append(f"{sc['name']}: {u['calls']} calls, "
                                       f"{u['total_tokens']} tokens ({u['prompt_tokens']} in / {u['completion_tokens']} out)")
                results.append({"scenario": sc["name"], "provider": prov_name,
                                "run_id": run_id, "status": status,
                                "steps": final.get("steps"), "denied": denied})

    report.render(RUNS_DIR, PROJECT_ROOT / "trace.html")
    print(f"\n[demo] trace report: {PROJECT_ROOT / 'trace.html'}", flush=True)
    for n in spend_notes:
        print(f"[spend] {n}", file=sys.stderr, flush=True)
    return 0 if all(r["status"] == "PASS" for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))