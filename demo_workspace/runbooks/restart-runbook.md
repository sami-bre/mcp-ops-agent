# Runbook: restarting payments-api

Owner: platform-oncall · Scope: the `demo` namespace in the ops-demo k3d cluster

## Symptoms
- `payments-api` pod in `CrashLoopBackOff`
- logs end with `PaymentError: redis connection refused`

## Steps (human operator)
1. Confirm the failing pod:
   `kubectl get pods -n demo`
2. Read its recent logs:
   `kubectl logs -n demo payments-api-<hash> --tail=50`
3. Roll the deployment (mutating — a human operator step, NOT the agent:
   the mcp-ops-agent kubectl tools are read-only get/describe/logs):
   `kubectl rollout restart deploy/payments-api -n demo`
4. Verify recovery:
   `kubectl get pods -n demo -w` until the pod is `Running`.

## Escalation
If redis itself is down, restart redis first, then repeat step 3.