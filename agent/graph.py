"""LangGraph ReAct agent wired to the MCP tools.

        ┌──────────────────────────────────────────────┐
        ▼                                              │ (not done, steps < MAX)
   [reason] ── action? ──► [act] ── observation ────────┤
        │                                              │
        └─ finish? ──► [finalize]                      │ (steps >= MAX → finalize)

Every transition writes trace events (llm_request / llm_response / tool_call /
tool_result / guard_denied / run_finish) via the TraceRecorder, and the runner
streams node updates as they happen.
"""

from __future__ import annotations

import json
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph

from agent.llm import Provider, parse_agent_json
from agent.mcp_client import McpTools
from trace.events import TraceRecorder

MAX_STEPS = 8


class AgentState(TypedDict):
    task: str
    history: Annotated[list[dict], lambda a, b: b]  # overwrite semantics
    roster: str
    steps: int
    done: bool
    answer: str
    pending_action: dict | None
    error: str


def build_roster(tool_schemas: list[dict]) -> str:
    lines = []
    for t in tool_schemas:
        props = (t.get("input_schema") or {}).get("properties", {}) or {}
        params = ", ".join(
            f"{k}:{(v.get('type') or 'any')}"
            + (f"={json.dumps(v['enum'])}" if v.get("enum") else "")
            for k, v in props.items())
        lines.append(f"- {t['name']}({params or 'no args'}): {t['description']}")
    return "\n".join(lines)


SYSTEM_PROMPT = """You are an operations agent. You investigate tasks using ONLY \
the tools below (via an MCP server with strict guardrails). Be concise.

Available tools:
{roster}

Rules:
- Reply with STRICT JSON only, no prose outside the JSON, in one of these shapes:
  {{"thought": "...", "action": {{"tool": "<tool name>", "args": {{...}}}}}}
  {{"thought": "...", "finish": "<final answer to the user>"}}
- Use at most a few tool calls; finish as soon as you can answer.
- If a call is denied or errors, adapt or finish — never retry the same denied call.
- If asked to do something destructive or outside the tools' capability, finish \
and explain that the guardrails prevent it.
"""


def _truncate(text: str, limit: int = 1500) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [truncated, {len(text)} chars total]"


def build_graph(provider: Provider, tools: McpTools,
                recorder: TraceRecorder, max_steps: int = MAX_STEPS):
    """Compile the agent graph. provider: MockLLM or OpenRouterLLM."""

    async def reason(state: AgentState) -> dict:
        messages = [{"role": "system",
                     "content": SYSTEM_PROMPT.format(roster=state["roster"])}]
        messages += state["history"]
        recorder.emit("llm_request", node="reason",
                      provider=provider.name, model=provider.model,
                      messages_preview=_truncate(
                          "\n".join(m["content"] for m in messages), 2000))
        reply: dict[str, Any]
        try:
            reply = provider.complete(messages)
        except Exception as exc:
            recorder.emit("llm_response", node="reason",
                          error=f"{type(exc).__name__}: {exc}")
            return {"done": True, "error": f"LLM call failed: {exc}",
                    "answer": "Run ended: the LLM call failed."}
        recorder.emit("llm_response", node="reason", reply=reply)

        if reply.get("finish"):
            return {"done": True, "answer": str(reply["finish"])}
        action = reply.get("action") or {}
        tool, args = action.get("tool"), action.get("args")
        if not tool or not isinstance(args, dict):
            return {"done": True, "error": "malformed action",
                    "answer": "Run ended: the LLM produced a malformed action."}
        return {"pending_action": {"tool": tool, "args": args}}

    async def act(state: AgentState) -> dict:
        action = state.get("pending_action") or {}
        tool, args = action.get("tool"), action.get("args") or {}
        recorder.emit("tool_call", node="act", tool=tool, args=args)
        raw = await tools.call(tool, args)
        try:
            envelope = json.loads(raw)
        except json.JSONDecodeError:
            envelope = {"ok": False, "denied": False, "error": f"non-JSON reply: {raw[:200]}"}
        if envelope.get("denied"):
            recorder.emit("guard_denied", node="act", tool=tool, args=args,
                          reason=envelope.get("error", "denied"))
        else:
            recorder.emit("tool_result", node="act", tool=tool, ok=envelope.get("ok", False),
                          error=envelope.get("error"),
                          result_preview=_truncate(str(envelope.get("result", ""))))
        history = list(state["history"])
        history.append({"role": "assistant",
                        "content": json.dumps({"thought": "", "action": action})})
        history.append({"role": "user",
                        "content": "observation: " + _truncate(raw, 4000)})
        return {"history": history, "steps": state["steps"] + 1,
                "pending_action": None}

    def route_after_reason(state: AgentState) -> str:
        if state["done"] or state["steps"] >= max_steps:
            return "finalize"
        return "act"

    async def finalize(state: AgentState) -> dict:
        if not state["done"] and state["steps"] >= max_steps and not state["error"]:
            recorder.emit("run_finish", node="finalize", reason="max_steps_reached")
            return {"done": True,
                    "answer": "Stopped after the step limit; no final answer produced."}
        recorder.emit("run_finish", node="finalize", answer=state["answer"],
                      error=state["error"])
        return {}

    g = StateGraph(AgentState)
    g.add_node("reason", reason)
    g.add_node("act", act)
    g.add_node("finalize", finalize)
    g.add_edge(START, "reason")
    g.add_conditional_edges("reason", route_after_reason,
                            {"act": "act", "finalize": "finalize"})
    g.add_edge("act", "reason")
    g.add_edge("finalize", END)
    return g.compile()


async def run_task(task: str, provider: Provider, tools: McpTools,
                   recorder: TraceRecorder, max_steps: int = MAX_STEPS) -> dict:
    """Run one task end-to-end; returns the final state (answer, steps, ...)."""
    recorder.emit("run_start", task=task, provider=provider.name,
                  model=provider.model)
    graph = build_graph(provider, tools, recorder, max_steps=max_steps)
    init: AgentState = {
        "task": task,
        "history": [{"role": "user", "content": f"task: {task}"}],
        "roster": build_roster(tools.tool_schemas),
        "steps": 0,
        "done": False,
        "answer": "",
        "pending_action": None,
        "error": "",
    }
    final = dict(init)
    async for update in graph.astream(init, stream_mode="updates"):
        for node, delta in update.items():
            if not delta:
                continue
            final.update(delta)
            print(f"[agent] {node} -> steps={final.get('steps')}", file=__import__('sys').stderr, flush=True)
    return final