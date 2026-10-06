# Oncall notes — 2026-09-30 incident

14:02 pager: payments-api CrashLoopBackOff in demo namespace.
14:05 redis pod had been OOM-killed during a load test; payments-api exits at
      boot when redis is unreachable (see app/main.py connect_redis).
14:20 redis restarted, payments-api rolled via the runbook, recovered 14:22.

Action item: add retry/backoff to the redis connection at boot
(runbooks/restart-runbook.md step 3 stays a human step — agent tools are
read-only by design).