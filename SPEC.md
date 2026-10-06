# SPEC.md — mcp-ops-agent (portfolio demo #2)

> Pitch (fixed, from portfolio guide §5): *"Built an MCP server exposing real, guarded
> tools and wired a LangGraph agent to it — with streamed agent traces you can audit,
> on a reproducible local k3s cluster."*

A Model Context Protocol (MCP) server exposing a small set of **real but guarded**
ops tools — file search, a sandboxed shell, read-only kubectl — wired to a **LangGraph
ReAct agent** whose every step (LLM request/response, tool call, args, result, guard
denial) is streamed into an auditable JSONL trace and rendered as a self-contained HTML
trace report. Runs out of the box against a mocked LLM (deterministic, zero API spend)
and against a real LLM (OpenRouter, cheap flash-class model) at final verification.
A reproducible k3d cluster gives the kubectl tools real data to read.

## 1. Tool surface (MCP server, official `mcp` SDK, stdio transport)

Server: `ops_mcp/server.py`, launched as a subprocess by the agent client
(`agent/mcp_client.py`) using `mcp.client.stdio`. Tools are declared with JSON-schema
input validation via the SDK.

| tool | args | what it does |
| --- | --- | --- |
| `search_files` | `pattern` (regex), `glob` (default `**/*`) | regex search across file contents inside the demo workspace; returns `path:lineno: line` hits (capped) |
| `read_file` | `path`, `max_lines` (default 120) | reads a workspace file, capped output |
| `run_shell` | `command` (string) | runs a **read-only allowlisted** command inside the workspace sandbox |
| `kubectl_get` | `resource`, `name?`, `namespace?`, `output?` (`wide\|json`) | read-only `kubectl get` |
| `kubectl_describe` | `resource`, `name?`, `namespace?` | read-only `kubectl describe` |
| `kubectl_logs` | `pod`, `namespace?`, `tail?` (default 60) | read-only `kubectl logs` |

## 2. Guardrail design

Principle: **guards live in the tool layer, not in the prompt.** The LLM never sees a
guardrail it could argue with; a violating call is simply denied at execution time and
the denial is both returned to the model as an observation and written to the trace as a
`guard_denied` event.

1. **Workspace confinement (file tools + shell).** Every path argument is
   `realpath`-resolved and must be inside the workspace root (`demo_workspace/`).
   Rejects: absolute paths outside the root, `..` escapes, symlink traversal, `~`.
2. **Sandboxed shell.** Parsed with `shlex` (no `shell=True`), executed with
   `cwd=workspace`, 10s timeout, 8KB output cap.
   - Allowlist of binaries (read-only): `ls cat head tail wc grep find du stat sort uniq date`.
   - Deny list of metacharacters: `; | & > < \` $( ) $HOME` and friends — no chaining,
     no redirection, no command substitution.
   - Deny list of binaries regardless of position: `rm mv cp chmod chown curl wget
     sudo apt pip python kubectl` (kubectl is reachable only through its own
     fixed-argv tool).
   - Path args must stay inside the workspace (same confinement as above).
3. **Read-only kubectl.** Tools are **fixed-argv constructions**: the user-supplied
   values only fill validated slots (`resource` must match
   `^[a-z][a-z0-9-]*$` and be one of the known kinds; `name`/`namespace` must match
   `^[a-zA-Z0-9][a-zA-Z0-9._-]*$`). No flags are ever taken from the model — so
   `kubectl delete/apply/edit/patch/exec` is unreachable by construction. If the
   cluster is down the tool returns a structured `cluster_unavailable` error instead
   of failing the run.
4. **Secret-file deny list (file tools + shell `cat`-type reads).** Files matching
   `*.env`, `.env*`, `*.pem`, `*id_rsa*`, `*secret*`, `*.key`, `credentials*` are
   refused for reading and skipped by `search_files`. A decoy `demo_workspace/secrets/staging.env`
   exists purely so the demo can show the guard firing.

## 3. Agent graph (LangGraph)

`agent/graph.py` — a ReAct-style loop built on `langgraph` `StateGraph`:

```
        ┌──────────────────────────────────────────────┐
        ▼                                              │ (not done, steps < MAX)
   [reason] ── action? ──► [act] ── observation ────────┤
        │                                              │
        └─ finish? ──► [finalize]                      │ (steps == MAX → finalize)
```

- **State** (`TypedDict`): `task`, `history` (list of `{role, content}` messages),
  `steps`, `done`, `answer`, `error`.
- **reason** — sends the task, the tool roster (names + one-line descriptions +
  arg schemas) and recent observations to the LLM; expects a strict JSON reply:
  `{"thought": "...", "action": {"tool": "...", "args": {...}}}` or
  `{"thought": "...", "finish": "final answer"}`. Malformed JSON → one repair
  retry, then the node finishes the run with an error answer.
- **act** — executes the chosen tool through the MCP client session; guard denials
  and tool errors are observations like `GUARD DENIED: <reason>` so the model can
  adapt. Unknown tool names are denied too.
- **finalize** — records the final answer (or max-steps message) and ends the run.
- Loop guard: `MAX_STEPS = 8`; the step counter lives in state, the conditional
  edge routes to `finalize` when exceeded.

**LLM providers** (`agent/llm.py`, one interface, two implementations):
- `MockLLM(script)` — replays a canned list of the same strict-JSON replies,
  deterministically; used for all development, unit tests, and `make demo` default.
  Zero API calls.
- `OpenRouterLLM(model, api_key)` — plain `httpx` POST to
  `https://openrouter.ai/api/v1/chat/completions` (OpenAI-compatible), temperature 0.
  Only used with `MCP_OPS_MODE=live` (final verification / shipped demo). Default
  model: `openrouter/google/gemini-2.5-flash` (cheap flash class, overridable via
  `MCP_OPS_MODEL`).

## 4. Trace format

Every run appends newline-delimited JSON events to `runs/<run_id>/trace.jsonl` and
the agent prints events to stderr as they happen (the "streamed" part). One event
per line:

```json
{"seq": 3, "ts": "2026-10-07T12:00:03.512Z", "run_id": "r-20261007-120001-a1b2",
 "event": "tool_call", "node": "act", "payload":
   {"tool": "kubectl_get", "args": {"resource": "pods", "namespace": "demo"}}}
```

Event types: `run_start`, `llm_request`, `llm_response`, `tool_call`,
`tool_result`, `guard_denied`, `run_finish`. Payloads carry full args, truncated
results (with `truncated: true` markers) and denial reasons — every tool call, its
arguments and its result are visible in the trace.

`trace/report.py` renders one or more runs into a **self-contained HTML** trace
report (`trace.html`, no external assets, same style bar as ragbench-lite's
report.html): run header with task/answer/step count/duration, a timeline of events
with expandable `<details>` per event, `guard_denied` rows highlighted, and a
per-run tool-call tally.

## 5. Demo script (`make demo`)

1. Ensures deps (`.venv`).
2. Runs the agent over three scenarios from `eval/scenarios.jsonl` **with the mock
   LLM** (default; deterministic, zero spend):
   - *cluster health* — which pods aren't Running in the `demo` namespace, and what
     does the crash-looping pod's log tail show → `kubectl_get` → `kubectl_logs` → finish.
   - *runbook lookup* — find the restart runbook for payments-api and summarize the
     steps → `search_files` → `read_file` → finish.
   - *guardrail* — asked to "delete the payments-api deployment"; the agent tries a
     destructive path, the tool layer denies it (read-only kubectl / shell deny list /
     secret-file deny list are all shown firing across the demo) → trace shows
     `guard_denied` events and the run ends safely.
3. Renders `trace.html` from the run traces.
4. Re-running with `MCP_OPS_MODE=live OPENROUTER_API_KEY=sk-... make demo` does the
   same three tasks through the real LLM (spend logged in `DEMO_LOG.md`).

Cluster piece (`make cluster-up`): installs docker + k3d if missing, creates a
1-node k3d cluster `ops-demo`, applies `k3d/manifests/*.yaml` — namespace `demo`
with `payments-api` (deliberately crash-looping so logs are interesting), `web`
(nginx), `redis`. `make cluster-down` tears it down. If the cluster isn't up, the
kubectl tools return `cluster_unavailable` and the demo still runs end-to-end with
the file/shell tools.

## 6. k3d reproducibility story

`k3d/bootstrap.sh` is idempotent: checks for docker/k3d/kubectl, installs missing
pieces (docker via apt, k3d single binary from GitHub releases, kubectl via k3d's
bundled path guidance), creates the cluster with a fixed name and port mapping, then
`kubectl apply -f k3d/manifests/`. Anyone with docker can rebuild the exact demo
cluster with one command. Honest-status rule: if cluster bootstrapping fails on a
given box, everything except kubectl-backed scenarios still works, and the README
says so explicitly — cluster output is never faked.

## 7. Testing strategy (mock-first)

- `tests/test_guards.py` — unit tests for path confinement, shell allowlist/deny
  list, metacharacter rejection, secret-file deny list, kubectl argv validation
  (all against the real guard implementations).
- `tests/test_agent_mock.py` — end-to-end agent runs against `MockLLM` scripts +
  an in-process MCP server (stdio): happy path, guard-denial path, max-steps path;
  asserts trace events exist and the answer is final.
- `tests/test_trace.py` — JSONL round-trip and HTML report generation (contains
  event markers, escapes payload HTML).
- Mock during dev (key policy); real OpenRouter calls only at final verification.

## 8. Repository layout

```
mcp-ops-agent/
├── SPEC.md                  # this file
├── README.md                # half engineering, half client-facing
├── Makefile                 # venv / test / demo / trace / cluster-up / cluster-down
├── ops_mcp/server.py        # MCP server (FastMCP) with the six guarded tools
├── ops_mcp/guards.py        # path confinement, shell allow/deny, kubectl argv validation
├── agent/graph.py           # LangGraph ReAct loop
├── agent/mcp_client.py      # stdio client session wrapper
├── agent/llm.py             # MockLLM / OpenRouterLLM
├── trace/events.py          # trace writer (JSONL + stderr streaming)
├── trace/report.py          # self-contained HTML trace report
├── eval/scenarios.jsonl     # demo scenarios (task + mock script + expectations)
├── k3d/bootstrap.sh         # idempotent docker+k3d+cluster bootstrap
├── k3d/manifests/*.yaml     # demo namespace: payments-api (crashloop), web, redis
├── demo_workspace/          # sandbox for file/shell tools (incl. decoy secrets)
├── runs/                    # trace artifacts (gitignored)
├── trace.html               # generated report (gitignored)
└── tests/                   # pytest, mock-only
```

## 9. Non-goals / honesty rules

- No write access anywhere: no tool can mutate the workspace, the host, or the cluster.
- The demo never fakes cluster output: if k3d isn't up, traces show the
  `cluster_unavailable` error, not invented pod lists.
- The OpenRouter key is demo-runtime-only: all build/test iteration uses `MockLLM`;
  live spend is logged per-run in `DEMO_LOG.md`.