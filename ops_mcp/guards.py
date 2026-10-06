"""Guardrails for the mcp-ops-agent MCP server.

Principle: guards live in the tool layer, not in the prompt. A violating call
is denied at execution time; the denial goes back to the model as an
observation AND into the trace as a guard_denied event.
"""

from __future__ import annotations

import fnmatch
import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path


class GuardError(Exception):
    """Raised when a tool call violates a guardrail (deny=True) or hits a
    structured, non-guard failure such as cluster_unavailable (deny=False)."""

    def __init__(self, reason: str, deny: bool = True):
        self.reason = reason
        self.deny = deny
        super().__init__(reason)


# --------------------------------------------------------------------------
# Workspace confinement
# --------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_WORKSPACE = PROJECT_ROOT / "demo_workspace"


def workspace_root() -> Path:
    root = os.environ.get("MCP_OPS_WORKSPACE")
    return Path(root).resolve() if root else DEFAULT_WORKSPACE.resolve()


def resolve_in_workspace(path: str) -> Path:
    """Resolve *path* and require it to stay inside the workspace root.

    Rejects absolute paths outside the root, '..' escapes, symlink traversal
    (via realpath) and '~' expansion.
    """
    if not isinstance(path, str) or not path.strip():
        raise GuardError("empty or non-string path")
    if path.startswith("~"):
        raise GuardError("'~' paths are not allowed")
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = workspace_root() / candidate
    resolved = candidate.resolve()  # collapses '..' and symlinks
    root = workspace_root().resolve()
    if resolved != root and root not in resolved.parents:
        raise GuardError(
            f"path escapes the sandbox workspace ({resolved} is outside {root})"
        )
    return resolved


# --------------------------------------------------------------------------
# Secret-file deny list
# --------------------------------------------------------------------------

SECRET_FILE_PATTERNS = [
    ".env*", "*.env", "*.pem", "*id_rsa*", "*id_ed25519*",
    "*secret*", "*.key", "credentials*", "*.p12", "*.pfx", "*.pem",
]


def is_secret_file(path: Path) -> bool:
    """True if the file name matches the secret deny list."""
    name = path.name
    return any(fnmatch.fnmatch(name, pat) for pat in SECRET_FILE_PATTERNS)


def check_not_secret(path: Path) -> None:
    if is_secret_file(path):
        raise GuardError(
            f"secret-file deny list: '{path.name}' matches {SECRET_FILE_PATTERNS} "
            "and cannot be read"
        )


# --------------------------------------------------------------------------
# Sandboxed shell
# --------------------------------------------------------------------------

SHELL_ALLOWLIST = {
    "ls", "cat", "head", "tail", "wc", "grep", "find", "du",
    "stat", "sort", "uniq", "date",
}
SHELL_DENY_BINARIES = {
    "rm", "mv", "cp", "chmod", "chown", "ln", "mkfs", "dd", "shred",
    "curl", "wget", "nc", "ssh", "scp", "sudo", "su", "apt", "apt-get",
    "pip", "pip3", "python", "python3", "kubectl", "docker", "k3d",
    "bash", "sh", "zsh", "env", "xargs", "tee", "kill", "pkill",
}
SHELL_METACHARS = set(";|&><`$()\n\\")


def parse_shell_command(command: str) -> list[str]:
    """Parse and validate a shell command; returns the argv to execute."""
    if not isinstance(command, str) or not command.strip():
        raise GuardError("empty or non-string command")
    if len(command) > 500:
        raise GuardError("command too long (max 500 chars)")
    for ch in command:
        if ch in SHELL_METACHARS:
            raise GuardError(f"shell metacharacter {ch!r} is not allowed "
                             "(no chaining, redirection or substitution)")
    argv = shlex.split(command)
    if not argv:
        raise GuardError("empty command")
    binary = Path(argv[0]).name
    if "/" in argv[0]:
        raise GuardError("commands must be bare binary names, no paths")
    if binary in SHELL_DENY_BINARIES:
        raise GuardError(f"binary '{binary}' is on the deny list")
    if binary not in SHELL_ALLOWLIST:
        raise GuardError(
            f"binary '{binary}' is not on the allowlist {sorted(SHELL_ALLOWLIST)}"
        )
    for arg in argv[1:]:
        for ch in ("~",):
            if arg.startswith(ch):
                raise GuardError(f"'~' expansion is not allowed (arg {arg!r})")
        # any absolute path argument must stay inside the workspace
        if arg.startswith("/"):
            if not arg.startswith(str(workspace_root())):
                raise GuardError(f"absolute path {arg!r} is outside the workspace")
        if ".." in Path(arg).parts:
            raise GuardError(f"'..' in argument {arg!r} is not allowed")
    return argv


def run_sandboxed_shell(command: str, timeout: float = 10.0,
                        max_output: int = 8192) -> str:
    """Execute an allowlisted read-only command inside the workspace."""
    argv = parse_shell_command(command)
    binary = shutil.which(argv[0])
    if binary is None:
        raise GuardError(f"binary '{argv[0]}' not found on this machine")
    try:
        proc = subprocess.run(
            argv, cwd=workspace_root(), timeout=timeout,
            capture_output=True, text=True, shell=False,
        )
    except subprocess.TimeoutExpired:
        raise GuardError(f"command timed out after {timeout}s")
    out = (proc.stdout + (("\n[stderr] " + proc.stderr) if proc.stderr.strip() else ""))
    truncated = False
    if len(out) > max_output:
        out, truncated = out[:max_output], True
    header = f"$ {command}\n(exit code {proc.returncode}"
    if truncated:
        header += ", output truncated"
    header += ")\n"
    return header + out


# --------------------------------------------------------------------------
# Read-only kubectl argv validation
# --------------------------------------------------------------------------

KUBE_RESOURCE_RE = re.compile(r"^[a-z][a-z0-9-]{0,62}$")
KUBE_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,251}$")
KUBE_GET_RESOURCES = {
    "pods", "deployments", "services", "nodes", "events", "namespaces",
    "replicasets", "configmaps", "endpoints", "statefulsets", "jobs", "cronjobs",
}
KUBE_DESCRIBE_RESOURCES = KUBE_GET_RESOURCES
KUBE_OUTPUTS = {"wide", "json"}
KUBECTL_BIN = os.environ.get("MCP_OPS_KUBECTL", "kubectl")


def _kube_slot(value: str, what: str, regex: re.Pattern) -> str:
    if not isinstance(value, str) or not regex.match(value):
        raise GuardError(f"invalid {what}: {value!r}")
    return value


def kubectl_get_argv(resource: str, name: str | None = None,
                     namespace: str | None = None, output: str | None = None
                     ) -> list[str]:
    """Build the fixed argv for a read-only kubectl get. No model-supplied flags."""
    _kube_slot(resource, "resource kind", KUBE_RESOURCE_RE)
    if resource not in KUBE_GET_RESOURCES:
        raise GuardError(
            f"resource kind '{resource}' not allowed; choose from {sorted(KUBE_GET_RESOURCES)}"
        )
    argv = [KUBECTL_BIN, "get", resource]
    if name is not None:
        argv.append(_kube_slot(name, "resource name", KUBE_NAME_RE))
    if namespace is not None:
        argv += ["-n", _kube_slot(namespace, "namespace", KUBE_NAME_RE)]
    if output is not None:
        if output not in KUBE_OUTPUTS:
            raise GuardError(f"output must be one of {sorted(KUBE_OUTPUTS)}")
        argv += ["-o", output]
    return argv


def kubectl_describe_argv(resource: str, name: str | None = None,
                          namespace: str | None = None) -> list[str]:
    _kube_slot(resource, "resource kind", KUBE_RESOURCE_RE)
    if resource not in KUBE_DESCRIBE_RESOURCES:
        raise GuardError(
            f"resource kind '{resource}' not allowed; choose from {sorted(KUBE_DESCRIBE_RESOURCES)}"
        )
    argv = [KUBECTL_BIN, "describe", resource]
    if name is not None:
        argv.append(_kube_slot(name, "resource name", KUBE_NAME_RE))
    if namespace is not None:
        argv += ["-n", _kube_slot(namespace, "namespace", KUBE_NAME_RE)]
    return argv


def kubectl_logs_argv(pod: str, namespace: str | None = None,
                      tail: int | None = None) -> list[str]:
    argv = [KUBECTL_BIN, "logs"]
    argv.append(_kube_slot(pod, "pod name", KUBE_NAME_RE))
    if namespace is not None:
        argv += ["-n", _kube_slot(namespace, "namespace", KUBE_NAME_RE)]
    if tail is not None:
        if not isinstance(tail, int) or not (1 <= tail <= 500):
            raise GuardError("tail must be an integer between 1 and 500")
        argv += ["--tail", str(tail)]
    return argv


def run_kubectl(argv: list[str], timeout: float = 15.0) -> str:
    """Run a pre-validated read-only kubectl argv; structured error if cluster is down."""
    try:
        proc = subprocess.run(argv, timeout=timeout, capture_output=True,
                              text=True, shell=False)
    except FileNotFoundError:
        raise GuardError("kubectl binary not found on this machine", deny=False)
    except subprocess.TimeoutExpired:
        raise GuardError("kubectl timed out")
    if proc.returncode != 0:
        err = proc.stderr.strip() or proc.stdout.strip() or "unknown error"
        if "connection refused" in err.lower() or "no configuration" in err.lower() \
                or "Unable to connect" in err:
            # not a guard denial: a structured environmental error
            raise GuardError(f"cluster_unavailable: {err}", deny=False)
        raise GuardError(f"kubectl failed: {err[:500]}")
    out = proc.stdout
    if len(out) > 8192:
        out = out[:8192] + "\n... [truncated]"
    return out