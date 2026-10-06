"""End-to-end agent tests: real MCP server subprocess + MockLLM (no API spend)."""

import asyncio
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.llm import MockLLM  # noqa: E402
from agent.mcp_client import open_mcp_tools  # noqa: E402
from agent.graph import run_task  # noqa: E402
from trace.events import TraceRecorder  # noqa: E402

WS = str(Path(__file__).resolve().parent.parent / "demo_workspace")


def run(task: str, script: list[dict]) -> tuple[dict, list[dict]]:
    async def _inner():
        async with open_mcp_tools(workspace=WS) as tools:
            run_id = f"test-{uuid.uuid4().hex[:6]}"
            recorder = TraceRecorder(run_id,
                runs_dir=Path(WS).parent / "runs" / "testtmp")
            final = await run_task(task, MockLLM(script), tools, recorder)
            return final, recorder.read_events()
    return asyncio.run(_inner())


def test_happy_path_search_and_read():
    script = [
        {"thought": "search", "action": {"tool": "search_files",
        "args": {"pattern": "restart", "glob": "**/*.md"}}},
        {"thought": "read", "action": {"tool": "read_file",
        "args": {"path": "runbooks/restart-runbook.md"}}},
        {"thought": "done", "finish": "The runbook has 4 steps."},
    ]
    final, events = run("find the restart runbook", script)
    assert final["done"] and final["answer"] == "The runbook has 4 steps."
    kinds = [e["event"] for e in events]
    assert kinds[0] == "run_start" and kinds[-1] == "run_finish"
    assert "llm_request" in kinds and "llm_response" in kinds
    tool_calls = [e["payload"]["tool"] for e in events if e["event"] == "tool_call"]
    assert tool_calls == ["search_files", "read_file"]
    results = [e for e in events if e["event"] == "tool_result"]
    assert all(r["payload"]["ok"] for r in results)


def test_guard_denial_flows_back_as_observation():
    script = [
        {"thought": "try rm", "action": {"tool": "run_shell",
        "args": {"command": "rm -rf deploy"}}},
        {"thought": "blocked; finish", "finish": "Destructive action denied."},
    ]
    final, events = run("delete stuff", script)
    denied = [e for e in events if e["event"] == "guard_denied"]
    assert len(denied) == 1
    assert "rm" in denied[0]["payload"]["reason"]
    assert final["done"]


def test_secret_file_read_denied():
    script = [
        {"thought": "read secrets", "action": {"tool": "read_file",
        "args": {"path": "secrets/staging.env"}}},
        {"thought": "blocked; finish", "finish": "Secrets are protected."},
    ]
    final, events = run("read the env file", script)
    denied = [e for e in events if e["event"] == "guard_denied"]
    assert len(denied) == 1
    assert "secret" in denied[0]["payload"]["reason"].lower()


def test_unknown_tool_denied():
    script = [
        {"thought": "try unknown", "action": {"tool": "kubectl_delete",
        "args": {"name": "payments-api"}}},
        {"thought": "blocked; finish", "finish": "No such tool."},
    ]
    final, events = run("delete the deployment", script)
    denied = [e for e in events if e["event"] == "guard_denied"]
    assert len(denied) == 1


def test_cluster_unavailable_is_error_not_crash():
    script = [
        {"thought": "get pods", "action": {"tool": "kubectl_get",
        "args": {"resource": "pods", "namespace": "demo"}}},
        {"thought": "no cluster; finish", "finish": "Cluster unavailable."},
    ]
    final, events = run("check pods", script)
    results = [e for e in events if e["event"] == "tool_result"]
    # on the CI/dev box without a cluster the tool errors cleanly (ok=false)
    assert results and results[0]["payload"]["ok"] is False
    assert final["done"] and "Cluster unavailable" in final["answer"]


def test_max_steps_guard():
    # script never finishes -> loop must stop at MAX_STEPS
    script = [{"thought": f"step {i}", "action": {"tool": "read_file",
              "args": {"path": "README.md"}}} for i in range(50)]
    final, events = run("never-ending task", script)
    assert final["done"]
    tool_calls = [e for e in events if e["event"] == "tool_call"]
    assert len(tool_calls) <= 8
    finishes = [e for e in events if e["event"] == "run_finish"]
    assert finishes and finishes[0]["payload"].get("reason") == "max_steps_reached" \
        or final["answer"].startswith("Stopped after the step limit")