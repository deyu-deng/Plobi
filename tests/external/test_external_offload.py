"""Hermetic tests for the external-agent offload channel (no CLI, no quota).

These pin the four judgement points that do not need a real spend: the permissive
mode is config and dial-downable (裁定 99 一 + 判据 4), an unpriceable cost is
``None`` and never ``0`` (判据 2 / R-073), the working directory can never default
into a repository (裁定 99 二.3), and offloading never relaxes *our* gate — the
negative half of 判据 3.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent import external_offload as offload  # noqa: E402


class _Completed:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


@pytest.fixture
def workdir(tmp_path):
    scratch = tmp_path / "offload"
    scratch.mkdir()
    return scratch


# ── 判据 4: 放开是配置，不是写死 ────────────────────────────────────────────

def test_permission_mode_defaults_open_but_is_overridable(monkeypatch):
    monkeypatch.delenv(offload.PERMISSION_MODE_ENV, raising=False)
    assert "--permission-mode=dontAsk" in offload.permission_flags()

    monkeypatch.setenv(offload.PERMISSION_MODE_ENV, "default")
    assert "--permission-mode=default" in offload.permission_flags()


def test_unknown_mode_never_invents_a_bypass(monkeypatch):
    monkeypatch.setenv(offload.PERMISSION_MODE_ENV, "yolo-please")
    assert offload.permission_flags() == ("-y", "--permission-mode=dontAsk")


def test_every_advertised_mode_is_reachable(monkeypatch):
    for mode in offload.PERMISSION_MODES:
        monkeypatch.setenv(offload.PERMISSION_MODE_ENV, mode)
        assert f"--permission-mode={mode}" in offload.permission_flags()


def test_sandbox_claim_follows_config_not_wish(workdir, monkeypatch):
    monkeypatch.delenv(offload.SANDBOX_ENV, raising=False)
    assert offload.is_sandboxed() is False
    monkeypatch.setenv(offload.SANDBOX_ENV, "1")
    assert offload.is_sandboxed() is True


# ── 判据 2: 四件齐，且算不出的钱报 null ─────────────────────────────────────

def test_run_reports_who_where_flags_and_usage(workdir, monkeypatch):
    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = list(argv)
        seen["cwd"] = kwargs["cwd"]
        (Path(seen["cwd"]) / "out.txt").write_text("done\n", encoding="utf-8")
        return _Completed(stdout=json.dumps({"result": "ok", "usage": {"input_tokens": 12}}))

    monkeypatch.setattr(offload, "resolve_command", lambda agent="codebuddy": "/usr/bin/codebuddy")
    monkeypatch.setattr(offload.subprocess, "run", fake_run)

    result = offload.run_task("write a file", cwd=workdir)

    assert result.ok is True
    assert result.agent == "codebuddy"
    assert result.cwd == str(workdir.resolve())
    assert result.permission_flags
    assert result.usage == {"input_tokens": 12}
    assert "-p" in seen["argv"] and "--output-format" in seen["argv"]
    assert result.artifacts and result.artifacts[0]["sha256_12"]


def test_unpriceable_cost_is_none_and_never_zero(workdir, monkeypatch):
    monkeypatch.setattr(offload, "resolve_command", lambda agent="codebuddy": "/usr/bin/codebuddy")
    monkeypatch.setattr(
        offload.subprocess, "run",
        lambda *a, **k: _Completed(stdout=json.dumps({"result": "hi", "usage": {"input_tokens": 5}})),
    )
    result = offload.run_task("say hi", cwd=workdir)
    assert result.cost is None
    assert result.cost_status == "usage_reported_without_price"


def test_no_usage_reported_is_its_own_status(workdir, monkeypatch):
    monkeypatch.setattr(offload, "resolve_command", lambda agent="codebuddy": "/usr/bin/codebuddy")
    monkeypatch.setattr(offload.subprocess, "run", lambda *a, **k: _Completed(stdout="plain text"))
    result = offload.run_task("say hi", cwd=workdir)
    assert result.cost is None and result.cost_status == "no_usage_reported"
    assert result.text == "plain text"


# ── 裁定 99 二.3: 外包不许落在四个仓里 ─────────────────────────────────────

def test_repo_tree_is_refused_as_cwd(tmp_path):
    repo = tmp_path / "Plobi-Desktop" / "Code"
    repo.mkdir(parents=True)
    allowed, why = offload.cwd_is_allowed(repo)
    assert allowed is False and "repository tree" in why


def test_missing_cwd_is_refused(tmp_path):
    allowed, why = offload.cwd_is_allowed(tmp_path / "nope")
    assert allowed is False and "does not exist" in why


def test_run_task_refuses_repo_without_spawning(workdir, monkeypatch):
    repo = workdir / "Docs"
    repo.mkdir()
    calls = []
    monkeypatch.setattr(offload.subprocess, "run", lambda *a, **k: calls.append(a) or _Completed())
    result = offload.run_task("anything", cwd=repo)
    assert result.ok is False and calls == []
    assert result.cost_status == "not_started"


# ── 通道缺失时诚实报错，不假装可用 ──────────────────────────────────────────

def test_absent_agent_reports_failure_not_a_fake_success(workdir, monkeypatch):
    monkeypatch.setattr(offload, "resolve_command", lambda agent="codebuddy": "")
    result = offload.run_task("anything", cwd=workdir)
    assert result.ok is False and "no drivable command" in result.error
    assert result.exit_code is None


def test_marvis_is_not_advertised_as_a_channel():
    """Measured negative: MarvisAgent.exe is a server (--port_file/--log_dir/
    --home_dir, --work_mode) with no prompt/task flag, so it is not an offload
    target. If someone later adds it as an agent, this test must be replaced by
    evidence, not deleted."""
    assert "marvis" not in (offload.AGENT_CODEBUDDY,)
    assert offload.permission_flags("marvis") == ()


# ── 判据 3 的负例：放开外部权限不等于拆掉我方闸门 ──────────────────────────

def test_offload_argv_carries_no_plobi_side_bypass(workdir, monkeypatch):
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = list(argv)
        captured["env"] = kwargs["env"]
        return _Completed(stdout=json.dumps({"result": "x"}))

    monkeypatch.setattr(offload, "resolve_command", lambda agent="codebuddy": "/usr/bin/codebuddy")
    monkeypatch.setattr(offload.subprocess, "run", fake_run)
    offload.run_task("do it", cwd=workdir)

    blob = " ".join(captured["argv"])
    assert "pre_tool_call" not in blob
    assert "--allowedTools" not in blob  # we never widen what may run on our side
    assert "PLOBI_APPROVAL_DISABLED" not in captured["env"]


def test_plobi_gate_still_denies_without_an_approver(monkeypatch):
    """The other half of 判据 3: when the external agent calls back into a
    dangerous Plobi tool and nobody is there to approve, it is refused."""
    import tools.approval as approval

    from agent.codebuddy_acp_client import ask_approval

    called = {}

    def fake_gate(tool_name, reason, *, rule_key="", approval_callback=None):
        called["callback"] = approval_callback
        return {"approved": False, "message": "no approver present"}

    monkeypatch.setattr(approval, "request_tool_approval", fake_gate)
    monkeypatch.setattr("agent.codebuddy_acp_client._tls_approval_callback", lambda: None)

    assert ask_approval(None, "write_file", {"path": "x"}) == "deny"
    assert called["callback"] is None


def test_timeout_is_reported_not_swallowed(workdir, monkeypatch):
    monkeypatch.setattr(offload, "resolve_command", lambda agent="codebuddy": "/usr/bin/codebuddy")

    def explode(*a, **k):
        raise subprocess.TimeoutExpired(cmd=["codebuddy"], timeout=1)

    monkeypatch.setattr(offload.subprocess, "run", explode)
    result = offload.run_task("slow task", cwd=workdir, timeout=1)
    assert result.ok is False and "did not finish" in result.error


# ── 一手跑出来的形状差异：CodeBuddy 的 json 外壳是数组，agy 的是对象 ────────

def test_list_shaped_envelope_does_not_crash_a_finished_run():
    """Measured on this box: codebuddy --output-format json answers a LIST of turn
    records. Parsing it with an object-only reader raised AttributeError *after*
    the run had already completed and already spent — losing the evidence for
    nothing."""
    stdout = json.dumps([{"type": "system"}, {"result": "done", "usage": {"total_tokens": 7}}])
    text, usage = offload._parse_envelope(stdout)
    assert text == "done" and usage == {"total_tokens": 7}


def test_object_shaped_envelope_still_parses():
    text, usage = offload._parse_envelope(json.dumps({"result": "hi", "usage": {"total_tokens": 3}}))
    assert text == "hi" and usage == {"total_tokens": 3}


def test_unparseable_output_degrades_to_raw_text_not_an_exception():
    text, usage = offload._parse_envelope("not json at all")
    assert text == "not json at all" and usage == {}


def test_envelope_is_kept_for_audit_without_counting_as_a_product(workdir, monkeypatch):
    monkeypatch.setattr(offload, "resolve_command", lambda agent="codebuddy": "/usr/bin/codebuddy")

    def fake_run(argv, **kwargs):
        (Path(kwargs["cwd"]) / "made.txt").write_text("x", encoding="utf-8")
        return _Completed(stdout=json.dumps({"result": "ok"}))

    monkeypatch.setattr(offload.subprocess, "run", fake_run)
    result = offload.run_task("go", cwd=workdir)
    names = [Path(a["path"]).name for a in result.artifacts]
    assert names == ["made.txt"]
    assert (workdir / ".offload-envelope.json").is_file()
