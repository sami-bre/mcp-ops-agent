"""mcp-ops-agent MCP server — real, guarded ops tools over stdio.

Tools: search_files, read_file, run_shell, kubectl_get, kubectl_describe,
kubectl_logs. All guards live here (see guards.py); the model can never
bypass them because violating calls are denied at this layer.

Every tool answers with a JSON envelope:
  {"ok": true,  "tool": ..., "result": ...}
  {"ok": false, "tool": ..., "denied": true|false, "error": "..."}
`denied: true` marks a guardrail violation (vs. an ordinary tool error).
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from ops_mcp import guards

server = MCPServer(
    "mcp-ops-agent",
    title="mcp-ops-agent",
    description="Real but guarded ops tools: file search, sandboxed shell, "
                "read-only kubectl. Guards live in the tool layer.",
)


def _envelope(tool: str, fn, **kwargs) -> str:
    try:
        return json.dumps({"ok": True, "tool": tool, "result": fn(**kwargs)})
    except guards.GuardError as exc:
        return json.dumps({"ok": False, "tool": tool, "denied": exc.deny,
                           "error": (f"GUARD DENIED: {exc.reason}"
                                     if exc.deny else exc.reason)})
    except Exception as exc:  # ordinary tool error, not a guard denial
        return json.dumps({"ok": False, "tool": tool, "denied": False,
                           "error": f"{type(exc).__name__}: {exc}"})


RO = ToolAnnotations(readOnlyHint=True, destructiveHint=False)


@server.tool(annotations=RO)
def search_files(pattern: str, glob: str = "**/*") -> str:
    """Regex search across file contents inside the demo workspace.
    Returns up to 40 `path:lineno: line` hits. Skips secret-denied files."""
    def _run(pattern: str, glob: str) -> str:
        import re
        try:
            rx = re.compile(pattern)
        except re.error as exc:
            raise guards.GuardError(f"invalid regex: {exc}")
        root = guards.workspace_root()
        hits: list[str] = []
        skipped: list[str] = []
        for path in sorted(root.glob(glob)):
            if not path.is_file() or ".git" in path.parts:
                continue
            rel = path.relative_to(root)
            if guards.is_secret_file(path):
                skipped.append(str(rel))
                continue
            try:
                for lineno, line in enumerate(
                        path.read_text(errors="replace").splitlines(), 1):
                    if rx.search(line):
                        hits.append(f"{rel}:{lineno}: {line.strip()[:200]}")
                        if len(hits) >= 40:
                            break
            except OSError:
                continue
            if len(hits) >= 40:
                break
        summary = f"{len(hits)} hit(s)"
        if skipped:
            summary += f"; {len(skipped)} file(s) skipped by secret deny list"
        return "\n".join(hits) + f"\n[{summary}]" if hits else f"[{summary}]"
    return _envelope("search_files", _run, pattern=pattern, glob=glob)


@server.tool(annotations=RO)
def read_file(path: str, max_lines: int = 120) -> str:
    """Read a file inside the demo workspace (capped to max_lines, <= 200)."""
    def _run(path: str, max_lines: int) -> str:
        if not isinstance(max_lines, int) or not (1 <= max_lines <= 200):
            raise guards.GuardError("max_lines must be 1..200")
        resolved = guards.resolve_in_workspace(path)
        if not resolved.is_file():
            raise guards.GuardError(f"not a file: {path}")
        guards.check_not_secret(resolved)
        lines = resolved.read_text(errors="replace").splitlines()[:max_lines]
        return "\n".join(lines)
    return _envelope("read_file", _run, path=path, max_lines=max_lines)


@server.tool(annotations=RO)
def run_shell(command: str) -> str:
    """Run a read-only allowlisted command inside the workspace sandbox.
    Allowlist: ls cat head tail wc grep find du stat sort uniq date.
    No chaining/redirection/substitution; no binaries outside the allowlist."""
    return _envelope("run_shell", guards.run_sandboxed_shell, command=command)


@server.tool(annotations=RO)
def kubectl_get(resource: str, name: str | None = None,
                namespace: str | None = None, output: str | None = None) -> str:
    """Read-only `kubectl get`. resource in pods|deployments|services|nodes|
    events|namespaces|replicasets|configmaps|endpoints|statefulsets|jobs|cronjobs;
    output in wide|json. Flags are never taken from the caller."""
    def _run(**kw) -> str:
        argv = guards.kubectl_get_argv(**kw)
        return guards.run_kubectl(argv)
    return _envelope("kubectl_get", _run, resource=resource, name=name,
                     namespace=namespace, output=output)


@server.tool(annotations=RO)
def kubectl_describe(resource: str, name: str | None = None,
                     namespace: str | None = None) -> str:
    """Read-only `kubectl describe` for the same allowed resource kinds."""
    def _run(**kw) -> str:
        argv = guards.kubectl_describe_argv(**kw)
        return guards.run_kubectl(argv)
    return _envelope("kubectl_describe", _run, resource=resource, name=name,
                     namespace=namespace)


@server.tool(annotations=RO)
def kubectl_logs(pod: str, namespace: str | None = None, tail: int = 60) -> str:
    """Read-only `kubectl logs` for a pod (tail 1..500 lines)."""
    def _run(**kw) -> str:
        argv = guards.kubectl_logs_argv(**kw)
        return guards.run_kubectl(argv)
    return _envelope("kubectl_logs", _run, pod=pod, namespace=namespace, tail=tail)


def main() -> None:
    server.run(transport="stdio")


if __name__ == "__main__":
    main()