# DEMO_LOG.md — mcp-ops-agent run log

Tracks actual spend on the shared OpenRouter key (user-set policy: demo-runtime
agent calls only; leave >= $3 on the key).

## 2026-10-07 — build verification (live runs)

- Mode: `MCP_OPS_MODE=live`, model `google/gemini-3-flash-preview` (OpenRouter).
- 3 scenarios end-to-end against the live k3d cluster (namespace `demo`):
  - cluster-health: 3 LLM calls, 2328 tokens (2089 in / 239 out), 2 tool steps
  - runbook-lookup: 3 LLM calls, 2399 tokens (2053 in / 346 out), 2 tool steps
  - guardrails: 6 LLM calls, 4346 tokens (3965 in / 381 out), 4 tool attempts,
    1 guard denial (secret-file deny list fired on `secrets/staging.env`)
- Session total: 12 calls, 9073 tokens (~8.1k in / ~1.0k out).
  At `google/gemini-3-flash-preview` pricing ($0.50/M in, $3.00/M out):
  ≈ $0.013 for the full three-scenario live demo.
- One earlier live attempt (3 runs) failed fast with 400s (wrong model ID
  `openrouter/google/gemini-2.5-flash`); no tokens billed.
- Key balance after this build: $31.71 remaining (policy minimum $3 — OK).

Notes: all development/iteration/testing ran on the deterministic MockLLM
(zero API spend); live calls were only for final verification and the deployed
trace report.