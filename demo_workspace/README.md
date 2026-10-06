# payments-api — demo workspace

This directory is the sandbox for the mcp-ops-agent file and shell tools.
It simulates a small service checkout you might hand an ops agent:

- `app/main.py` — the payments-api entrypoint (has a boot-time redis dependency)
- `deploy/deployment.yaml` — the k8s manifest for the service
- `runbooks/restart-runbook.md` — human restart procedure
- `secrets/staging.env` — decoy secrets file (guarded: secret deny list)
- `ops/oncall-notes.md` — last incident notes

The kubectl tools are **read-only** (`get`/`describe`/`logs`); every mutating
k8s operation in the runbook is marked as a human step on purpose.