"""Stdio MCP client session wrapper used by the agent graph."""

from __future__ import annotations

import json
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class McpTools:
    """Owns the server subprocess + session; exposes list/call."""

    def __init__(self, workspace: str | None = None):
        env = dict(os.environ)
        env["MCP_OPS_WORKSPACE"] = workspace or str(PROJECT_ROOT / "demo_workspace")
        env["PYTHONUNBUFFERED"] = "1"
        self.params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "ops_mcp.server"],
            cwd=str(PROJECT_ROOT),
            env=env,
        )
        self.session: ClientSession | None = None
        self.tool_schemas: list[dict] = []
        self._cm = None

    async def start(self) -> None:
        self._cm = stdio_client(self.params)
        read, write = await self._cm.__aenter__()
        self.session = ClientSession(read, write)
        await self.session.__aenter__()
        await self.session.initialize()
        listed = await self.session.list_tools()
        self.tool_schemas = [
            {"name": t.name, "description": t.description or "",
             "input_schema": t.input_schema}
            for t in listed.tools
        ]

    async def call(self, tool: str, args: dict) -> str:
        """Call a tool; returns the JSON envelope text from the server.
        Unknown tools and transport errors are returned as envelopes too,
        so the agent always gets a clean observation."""
        if self.session is None:
            raise RuntimeError("MCP session not started")
        if tool not in {t["name"] for t in self.tool_schemas}:
            return json.dumps({"ok": False, "tool": tool, "denied": True,
                               "error": f"GUARD DENIED: unknown tool '{tool}' — "
                                        f"available: {sorted(t['name'] for t in self.tool_schemas)}"})
        try:
            result = await self.session.call_tool(tool, arguments=args or {})
        except Exception as exc:
            return json.dumps({"ok": False, "tool": tool, "denied": False,
                               "error": f"{type(exc).__name__}: {exc}"})
        parts = []
        for item in result.content:
            text = getattr(item, "text", None)
            if text is not None:
                parts.append(text)
        return "\n".join(parts) if parts else "(empty result)"

    async def stop(self) -> None:
        if self.session is not None:
            try:
                await self.session.__aexit__(None, None, None)
            except Exception:
                pass
            self.session = None
        if self._cm is not None:
            try:
                await self._cm.__aexit__(None, None, None)
            except Exception:
                pass
            self._cm = None


@asynccontextmanager
async def open_mcp_tools(workspace: str | None = None):
    tools = McpTools(workspace)
    await tools.start()
    try:
        yield tools
    finally:
        await tools.stop()