"""Unit tests for the guard layer (no LLM, no cluster)."""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ops_mcp import guards  # noqa: E402
from ops_mcp.guards import GuardError  # noqa: E402


# ---------- workspace confinement ----------

def test_read_inside_workspace_ok():
    p = guards.resolve_in_workspace("README.md")
    assert p.is_file()


def test_absolute_escape_denied():
    with pytest.raises(GuardError, match="escapes the sandbox"):
        guards.resolve_in_workspace("/etc/passwd")


def test_dotdot_escape_denied():
    with pytest.raises(GuardError, match="escapes the sandbox"):
        guards.resolve_in_workspace("../../etc/passwd")


def test_tilde_denied():
    with pytest.raises(GuardError, match="'~'"):
        guards.resolve_in_workspace("~/.bashrc")


# ---------- secret deny list ----------

def test_secret_file_denied(tmp_path):
    f = tmp_path / "staging.env"
    f.write_text("X=1")
    assert guards.is_secret_file(f)


def test_normal_file_not_secret(tmp_path):
    f = tmp_path / "main.py"
    f.write_text("print(1)")
    assert not guards.is_secret_file(f)


# ---------- shell guard ----------

def test_shell_allowlisted_ok():
    argv = guards.parse_shell_command("grep -n restart runbooks")
    assert argv[0] == "grep"


def test_shell_rm_denied():
    with pytest.raises(GuardError, match="deny list"):
        guards.parse_shell_command("rm -rf deploy")


def test_shell_unknown_binary_denied():
    with pytest.raises(GuardError, match="allowlist"):
        guards.parse_shell_command("htop --no-color")


def test_shell_chaining_denied():
    with pytest.raises(GuardError, match="metacharacter"):
        guards.parse_shell_command("ls; rm -rf /")


def test_shell_redirection_denied():
    with pytest.raises(GuardError, match="metacharacter"):
        guards.parse_shell_command("ls > /tmp/out.txt")


def test_shell_substitution_denied():
    with pytest.raises(GuardError, match="metacharacter"):
        guards.parse_shell_command("cat $(which python)")


def test_shell_path_arg_outside_workspace_denied():
    with pytest.raises(GuardError, match="outside the workspace"):
        guards.parse_shell_command("cat /etc/passwd")


def test_shell_dotdot_arg_denied():
    with pytest.raises(GuardError, match="'..'"):
        guards.parse_shell_command("cat ../MISSION.md")


def test_shell_kubectl_via_shell_denied():
    with pytest.raises(GuardError, match="deny list"):
        guards.parse_shell_command("kubectl delete pod x")


def test_sandboxed_shell_runs_in_workspace():
    out = guards.run_sandboxed_shell("ls")
    assert "app" in out or "runbooks" in out  # workspace contents
    assert "exit code 0" in out


def test_sandboxed_shell_rm_actually_denied():
    with pytest.raises(GuardError):
        guards.run_sandboxed_shell("rm -rf /tmp/demo-mcp-should-not-exist")
    assert not Path("/tmp/demo-mcp-should-not-exist").exists()


# ---------- kubectl argv validation ----------

def test_kubectl_get_valid():
    argv = guards.kubectl_get_argv("pods", namespace="demo")
    assert argv == ["kubectl", "get", "pods", "-n", "demo"]


def test_kubectl_get_name_and_output():
    argv = guards.kubectl_get_argv("pods", name="payments-api-7c9d",
                                   namespace="demo", output="json")
    assert argv[1:3] == ["get", "pods"]
    assert "-o" in argv and "json" in argv


def test_kubectl_get_unknown_kind_denied():
    with pytest.raises(GuardError, match="not allowed"):
        guards.kubectl_get_argv("secrets")


def test_kubectl_get_delete_impossible_by_construction():
    # 'delete' is not a resource kind and never a verb: argv builder cannot emit it
    with pytest.raises(GuardError):
        guards.kubectl_get_argv("delete")


def test_kubectl_bad_name_chars_denied():
    with pytest.raises(GuardError, match="invalid resource name"):
        guards.kubectl_get_argv("pods", name="a; rm -rf /")


def test_kubectl_logs_tail_bounds():
    assert guards.kubectl_logs_argv("web-abc", namespace="demo", tail=50)[0:2] == ["kubectl", "logs"]
    with pytest.raises(GuardError, match="tail"):
        guards.kubectl_logs_argv("web-abc", tail=0)
    with pytest.raises(GuardError, match="tail"):
        guards.kubectl_logs_argv("web-abc", tail=10_000)


def test_run_kubectl_cluster_unavailable(monkeypatch):
    # no cluster on this box -> structured cluster_unavailable GuardError
    import subprocess as sp
    def fake_run(argv, **kw):
        return sp.CompletedProcess(argv, 1, "", "E0107 connection refused")
    monkeypatch.setattr(guards.subprocess, "run", fake_run)
    with pytest.raises(GuardError, match="cluster_unavailable"):
        guards.run_kubectl(guards.kubectl_get_argv("pods"))