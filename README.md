# mcp-ops-agent

An MCP server exposing **real, guarded tools** (file search, sandboxed shell,
read-only kubectl) wired to a **LangGraph agent** — with **streamed agent traces
you can audit**, on a reproducible local k3s (k3d) cluster.

> For prospects: the point of this demo is trust. Agentic systems that touch real
> infrastructure need guardrails that live in the tool layer — not in a prompt the
> model can talk its way around — and traces that let a human audit every step.
> This repo shows both, end to end, in ~700 lines of readable Python.

Live demo: https://samibre.dev/mcp-ops-agent/ (self-contained trace report)

## What you're looking at

- **MCP server** (`ops_mcp/`, official `mcp` SDK, stdio) with 6 tools, every one
  guarded *at execution time*:
  - `search_files` / `read_file` — confined to the demo workspace by realpath;
    secret-shaped files (`.env*`, `*.pem`, `*id_rsa*`, …) are refused outright.
  - `run_shell` — parsed with `shlex`, executed without a shell, cwd=workspace:
    allowlisted read-only binaries only (`ls cat head tail wc grep find du stat
    sort uniq date`), no chaining/redirection/substitution, deny-listed binaries
    (`rm`, `curl`, `kubectl`, `python`, …) rejected wherever they appear.
  - `kubectl_get` / `kubectl_describe` / `kubectl_logs` — **fixed-argv
    constructions**: the model can only fill validated slots (resource kind,
    name, namespace); no flags are ever accepted, so `delete/apply/patch/exec`
    are unreachable by construction.
- **LangGraph agent** (`agent/graph.py`): ReAct loop — `reason` → `act` → loop →
  `finalize` — with a step limit. The LLM replies in strict JSON (action or
  finish). Guard denials come back to the model as observations, so it adapts
  instead of fighting the guardrail.
- **LLM providers** (`agent/llm.py`): a deterministic `MockLLM` (canned scripts,
  zero API spend — how all tests and the default demo run) and `OpenRouterLLM`
  (any cheap flash-class model) for live runs.
- **Traces** (`trace/`): every LLM request/response, tool call, args, result and
  guard denial is one JSONL event in `runs/<run_id>/trace.jsonl`, streamed to
  stderr as it happens, and rendered into a self-contained `trace.html` report —
  denials highlighted red, every payload expandable.

## Demo scenarios (`make demo`)

1. **cluster health** — list pods in the demo namespace, pull logs from the
   crash-looping one (`kubectl_get` → `kubectl_logs`).
2. **runbook lookup** — find and summarize the payments-api restart runbook
   (`search_files` → `read_file`).
3. **guardrails** — asked to delete a deployment and read the staging secrets
   file; both attempts are denied by the tool layer and the trace shows two red
   `guard_denied` events.

## Quick start

```bash
make venv            # .venv with mcp + langgraph + httpx
make demo            # mock-LLM demo -> trace.html (zero API spend)
make live-demo       # real LLM via OpenRouter: MCP_OPS_MODE=live is set for you
                     # (needs OPENROUTER_API_KEY in env; model: MCP_OPS_MODEL)
make cluster-up      # docker + k3d + kubectl + demo apps (idempotent)
make cluster-down
make test            # pytest: guard unit tests + mock end-to-end agent runs
```

`make cluster-up` builds the exact demo cluster: 1-node k3d cluster `ops-demo`,
namespace `demo`, with `web` (nginx), `redis`, and a deliberately
crash-looping `payments-api` — so the kubectl tools read **real** pod status and
**real** logs. Without the cluster, the kubectl tools return a structured
`cluster_unavailable` error and everything else still runs; the demo never fakes
cluster output.

## Layout

```
ops_mcp/server.py     MCP server: 6 guarded tools (guard denials are JSON envelopes)
ops_mcp/guards.py     path confinement, shell allow/deny, kubectl argv validation
agent/graph.py        LangGraph ReAct loop (reason/act/finalize, step limit)
agent/mcp_client.py   stdio MCP client session wrapper
agent/llm.py          MockLLM (deterministic) / OpenRouterLLM (live)
agent/run_demo.py     scenario runner: tasks -> traces -> checks
trace/events.py       JSONL trace writer (also streams to stderr)
trace/report.py       self-contained HTML trace report
eval/scenarios.jsonl  demo scenarios (task + mock script + expectations)
k3d/bootstrap.sh      idempotent docker + k3d + kubectl + demo-apps bootstrap
k3d/manifests/        namespace demo: payments-api (crashloop), web, redis
demo_workspace/       the sandbox the file/shell tools operate on
tests/                guard unit tests + mock end-to-end agent tests
```

## Honesty notes

- The `secrets/staging.env` in the workspace is a decoy written for the demo —
  the guard refuses to read it, which is the point.
- `make live-demo` calls a real LLM; on this project's OpenRouter key the spend
  is a few cents for the three scenarios and is logged in `DEMO_LOG.md`.
- The agent's kubectl surface is intentionally read-only; mutating ops in the
  runbook are documented as human steps.