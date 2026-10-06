"""LLM providers behind one interface.

- MockLLM: replays a canned strict-JSON script; deterministic, zero API spend.
  Used for all development/tests and as the `make demo` default.
- OpenRouterLLM: real completion via the OpenAI-compatible OpenRouter endpoint.
  Only used with MCP_OPS_MODE=live (demo runtime / final verification).

Both return parsed strict-JSON dicts:
  {"thought": "...", "action": {"tool": "...", "args": {...}}}
  {"thought": "...", "finish": "final answer text"}
"""

from __future__ import annotations

import json
import os
import re
from typing import Protocol

DEFAULT_OPENROUTER_MODEL = "google/gemini-3-flash-preview"


class Provider(Protocol):
    name: str
    model: str

    def complete(self, messages: list[dict]) -> dict: ...


class MockLLM:
    """Replays canned replies in order; raises if the script runs dry."""

    name = "mock"
    model = "canned-script"

    def __init__(self, script: list[dict]):
        self.script = list(script)
        self.calls = 0

    def complete(self, messages: list[dict]) -> dict:
        if not self.script:
            raise RuntimeError("MockLLM script exhausted — agent kept asking")
        self.calls += 1
        return self.script.pop(0)


class OpenRouterLLM:
    name = "openrouter"

    def __init__(self, model: str | None = None, api_key: str | None = None,
                 base_url: str = "https://openrouter.ai/api/v1"):
        self.model = model or os.environ.get(
            "MCP_OPS_MODEL", DEFAULT_OPENROUTER_MODEL)
        self.api_key = api_key or os.environ.get("OPENROUTER_API_KEY") or ""
        if not self.api_key:
            raise RuntimeError("OPENROUTER_API_KEY not set for live mode")
        self.base_url = base_url.rstrip("/")
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0,
                      "total_tokens": 0, "calls": 0}

    def complete(self, messages: list[dict]) -> dict:
        import httpx
        resp = httpx.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"model": self.model, "temperature": 0,
                  "max_tokens": 600, "messages": messages},
            timeout=60.0,
        )
        resp.raise_for_status()
        data = resp.json()
        usage = data.get("usage") or {}
        self.usage["prompt_tokens"] += usage.get("prompt_tokens", 0)
        self.usage["completion_tokens"] += usage.get("completion_tokens", 0)
        self.usage["total_tokens"] += usage.get("total_tokens", 0)
        self.usage["calls"] += 1
        content = data["choices"][0]["message"]["content"] or ""
        return parse_agent_json(content)


def parse_agent_json(content: str) -> dict:
    """Extract the strict-JSON agent reply (tolerates ```json fences)."""
    text = content.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # last resort: first {...} block
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            return json.loads(m.group(0))
        raise ValueError(f"LLM reply is not valid agent JSON: {content[:200]!r}")


def make_provider(recorder=None) -> Provider:
    """Mode switch: MCP_OPS_MODE=live -> OpenRouterLLM, else MockLLM caller-supplied."""
    if os.environ.get("MCP_OPS_MODE", "mock") == "live":
        return OpenRouterLLM()
    raise RuntimeError("make_provider in mock mode requires an explicit MockLLM")