"""payments-api — demo service entrypoint (used in the k3d demo cluster).

Deliberately crashes at boot when redis is unreachable, so the demo has a
real CrashLoopBackOff pod with real logs for the agent to investigate.
"""
import os
import sys
import time


class PaymentError(RuntimeError):
    pass


def connect_redis(url: str) -> None:
    # demo stub: fails when REDIS_URL points nowhere reachable at boot
    if not url:
        raise PaymentError("redis connection refused: REDIS_URL is empty")
    # a real client would connect here; the cluster image just exits 1


def main() -> None:
    print("[boot] payments-api starting", flush=True)
    time.sleep(0.2)
    print("[boot] connecting to redis...", flush=True)
    try:
        connect_redis(os.environ.get("REDIS_URL", ""))
    except PaymentError as exc:
        print(f"Traceback: PaymentError: {exc}", file=sys.stderr, flush=True)
        sys.exit(1)
    print("[boot] ready", flush=True)


if __name__ == "__main__":
    main()