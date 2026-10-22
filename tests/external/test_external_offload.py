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
    """The default must be the mode MEASURED to do the work, not the one that
    merely sounds permissive.  ``dontAsk`` here exits 0, spends ~114k input
    tokens and writes nothing — its meaning is "do not ask: deny"."""
    monkeypatch.delenv(offload.PERMISSION_MODE_ENV, raising=False)
    assert offload.DEFAULT_PERMISSION_MODE == "bypassPermissions"
    assert "--permission-mode=bypassPermissions" in offload.permission_flags()

    # The退路 stays: any advertised mode can be dialled back down, including
    # the one that was the default until it was measured.
    monkeypatch.setenv(offload.PERMISSION_MODE_ENV, "dontAsk")
    assert "--permission-mode=dontAsk" in offload.permission_flags()

    monkeypatch.setenv(offload.PERMISSION_MODE_ENV, "default")
    assert "--permission-mode=default" in offload.permission_flags()


def test_unknown_mode_never_invents_a_bypass(monkeypatch):
    """A typo must not silently land on the most open mode — now that the
    default IS the most open mode, substituting it would be an escalation."""
    monkeypatch.setenv(offload.PERMISSION_MODE_ENV, "yolo-please")
    with pytest.raises(ValueError) as exc:
        offload.permission_flags()
    assert "yolo-please" in str(exc.value)


def test_unknown_mode_refuses_before_anything_is_spawned(workdir, monkeypatch):
    """Fail closed and reportable: no subprocess, no spend, ok=False."""
    calls: list = []
    monkeypatch.setenv(offload.PERMISSION_MODE_ENV, "bypass-permissions")  # typo: dashes
    monkeypatch.setattr(
        offload.subprocess, "run",
        lambda *a, **k: calls.append(a) or _Completed(stdout="{}"),
    )
    result = offload.run_task("say hi", cwd=workdir)
    assert calls == [], "an invalid permission mode must not reach the child"
    assert result.ok is False
    assert result.cost_status == "not_started"
    assert "bypass-permissions" in result.error


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
    text, usage, session, source = offload._parse_envelope(stdout)
    assert text == "done" and usage == {"total_tokens": 7}
    assert session == "" and source == ""


def test_object_shaped_envelope_still_parses():
    text, usage, session, source = offload._parse_envelope(json.dumps({"result": "hi", "usage": {"total_tokens": 3}}))
    assert text == "hi" and usage == {"total_tokens": 3}
    assert session == "" and source == ""


def test_unparseable_output_degrades_to_raw_text_not_an_exception():
    text, usage, session, source = offload._parse_envelope("not json at all")
    assert text == "not json at all" and usage == {}
    assert session == "" and source == ""


# ── WP-OFFLOAD-SESSION-HANDLE：标识本来就在回包里，别再把它扔了 ──────────────

_CB_ID = "01a1248d-3b15-738e-977b-0b90cd206790"
_AGY_ID = "c6f16d79-d814-4c9d-8e74-6bbbe0877acb"


def _codebuddy_records(result_session_id=_CB_ID, process_session_id=_CB_ID):
    """The array shape measured on this box (see ``D:/Temp/offload-cb-20261010``):
    process records spell it ``sessionId``, only the closing ``result`` record
    spells it ``session_id``."""
    records = [
        {"type": "message", "sessionId": process_session_id, "text": "working"},
        {"type": "file-history-snapshot"},
        {"type": "function_call", "sessionId": process_session_id},
    ]
    if result_session_id:
        records.append({"type": "result", "session_id": result_session_id,
                        "result": "done", "usage": {"total_cost_usd": 0.0}})
    return records


def test_codebuddy_reads_the_closing_result_record_and_says_so():
    text, usage, session, source = offload._parse_envelope(json.dumps(_codebuddy_records()))
    assert session == _CB_ID
    assert source == "result.session_id"
    assert text.endswith("done")


def test_codebuddy_without_a_result_record_falls_back_to_the_process_key():
    text, usage, session, source = offload._parse_envelope(json.dumps(
        _codebuddy_records(result_session_id="")))
    assert session == _CB_ID
    assert source == "sessionId"


def test_the_two_codebuddy_spellings_are_reconciled_in_favour_of_result():
    """判据：两种拼法不一致时取 `result` 那条，**并把不一致说得出**——回跳要拿这个
    id 起 `--resume`，悄悄选一个就等于让前端猜厂商的信封长什么样。"""
    other = "01a19999-0000-7000-8000-000000000000"
    text, usage, session, source = offload._parse_envelope(json.dumps(
        _codebuddy_records(result_session_id=_CB_ID, process_session_id=other)))
    assert session == _CB_ID
    assert source == "result.session_id!=sessionId"


def test_agy_answers_one_object_with_its_own_key():
    text, usage, session, source = offload._parse_envelope(json.dumps(
        {"status": "ok", "response": "done", "conversation_id": _AGY_ID,
         "duration_seconds": 3.2, "usage": {"input_tokens": 10}}))
    assert session == _AGY_ID
    assert source == "conversation_id"
    assert text == "done"


def test_an_envelope_with_no_session_key_yields_empty_and_the_turn_still_lands():
    """形状变了不崩：读不到就是空，**空是读数不是失败**，面上也不许出现"未知会话"那种假话。"""
    stdout = json.dumps([{"type": "message", "text": "hi"}, {"type": "result", "result": "done"}])
    text, usage, session, source = offload._parse_envelope(stdout)
    assert text.endswith("done")
    assert session == "" and source == ""


def test_run_task_carries_the_handle_out_on_the_result(workdir, monkeypatch):
    monkeypatch.setattr(offload, "resolve_command", lambda agent="codebuddy": "/usr/bin/codebuddy")
    monkeypatch.setattr(offload, "egress_is_reachable", lambda proxy: (True, ""))
    stdout = json.dumps(_codebuddy_records())

    def fake_run(argv, **kwargs):
        (Path(kwargs["cwd"]) / "made.txt").write_text("x", encoding="utf-8")
        return _Completed(stdout=stdout)

    monkeypatch.setattr(offload.subprocess, "run", fake_run)
    result = offload.run_task("do it", cwd=workdir)
    assert result.ok is True
    assert result.external_session == _CB_ID
    assert result.external_session_source == "result.session_id"
    # as_dict 是消费侧真正读的那份形状
    assert result.as_dict()["external_session"] == _CB_ID


@pytest.mark.parametrize("evidence_dir, expected", [
    ("offload-cb-20261010", _CB_ID),
    ("offload-20261010", "01a12965-b059-75e9-97dd-72db6abfa98f"),
    ("plobi-offload-demo", "01a1257c-2376-7466-9222-7215052f5aed"),
    ("offload-product-a", _AGY_ID),
    ("offload-product-b", "afc613c9-18f3-44ca-9055-be85ef618977"),
    ("offload-20261011", "39e2a440-34ec-41cf-be7a-ae442fa18b6f"),
])
def test_every_envelope_kept_on_this_box_still_yields_its_id(evidence_dir, expected):
    """Real vendor output, zero spend — the判据 the task paper asked for ("只吃盘上现成的
    六份 envelope 当夹具"). These are this machine's audit files, so the test skips when
    they are not there; the inline fixtures above are the portable half."""
    path = Path(Path(offload.__file__).drive + "/Temp/" + evidence_dir
                + "/.offload-envelope.json")
    if not path.is_file():
        pytest.skip(f"no kept envelope for {evidence_dir} on this machine")
    text, usage, session, source = offload._parse_envelope(path.read_text(encoding="utf-8"))
    assert session == expected
    assert source in ("result.session_id", "sessionId", "conversation_id")


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


# ── 判据 6/7/8：代理注入位、不写死、连不上就当场说清（不许静默跑完） ─────────

def test_proxy_comes_from_the_knob_and_reaches_the_child(workdir, monkeypatch):
    seen = {}

    def fake_run(argv, **kwargs):
        seen["env"] = kwargs["env"]
        return _Completed(stdout=json.dumps({"result": "ok"}))

    monkeypatch.setenv(offload.PROXY_ENV, "http://proxy.invalid:8118")
    monkeypatch.setattr(offload, "egress_is_reachable", lambda url, timeout=3.0: (True, ""))
    monkeypatch.setattr(offload, "resolve_command", lambda agent="codebuddy": "/usr/bin/codebuddy")
    monkeypatch.setattr(offload.subprocess, "run", fake_run)
    result = offload.run_task("go", cwd=workdir)

    assert seen["env"]["HTTPS_PROXY"] == "http://proxy.invalid:8118"
    assert result.proxy_source == offload.PROXY_ENV


def test_egress_table_owns_the_child_network_keys(workdir, monkeypatch):
    """A declared ``network.egress`` table must reach the offload child through
    the SAME projector the terminal spawn uses — not a re-implementation.

    Asserts the three things that hand-writing proxy keys loses: the proxy comes
    from the catch-all, the hosts the table sends direct land in ``NO_PROXY``,
    and a stale inherited ``NO_PROXY`` cannot survive into the child.
    """
    import agent.egress.rules as egress_rules
    from agent.egress.rules import parse_table

    table = parse_table({
        "proxies": {"gw": {"url": "http://proxy.invalid:8118"}},
        "rules": [
            {"match": "github.com", "via": "direct"},
            {"match": "*", "via": "proxy:gw"},
        ],
    })
    monkeypatch.setattr(egress_rules, "load_table", lambda *a, **k: table)
    monkeypatch.setattr(offload, "egress_is_reachable", lambda url, timeout=3.0: (True, ""))
    monkeypatch.delenv(offload.PROXY_ENV, raising=False)
    monkeypatch.setenv("NO_PROXY", "internal.corp")
    monkeypatch.setenv("HTTPS_PROXY", "http://stale.invalid:1")

    seen = {}

    def fake_run(argv, **kwargs):
        seen["env"] = kwargs["env"]
        return _Completed(stdout=json.dumps({"result": "ok"}))

    monkeypatch.setattr(offload, "resolve_command", lambda agent="codebuddy": "/usr/bin/codebuddy")
    monkeypatch.setattr(offload.subprocess, "run", fake_run)
    result = offload.run_task("go", cwd=workdir)

    assert seen["env"]["HTTPS_PROXY"] == "http://proxy.invalid:8118"
    assert "github.com" in seen["env"]["NO_PROXY"]
    assert "internal.corp" not in seen["env"]["NO_PROXY"], "stale bypass must not survive"
    assert result.proxy_source == "network.egress"


def test_knob_outranks_the_table(workdir, monkeypatch):
    """The explicit per-channel knob is the user speaking louder than the table."""
    import agent.egress.rules as egress_rules
    from agent.egress.rules import parse_table

    table = parse_table({
        "proxies": {"gw": {"url": "http://proxy.invalid:8118"}},
        "rules": [{"match": "*", "via": "proxy:gw"}],
    })
    monkeypatch.setattr(egress_rules, "load_table", lambda *a, **k: table)
    monkeypatch.setenv(offload.PROXY_ENV, "http://knob.invalid:9999")
    monkeypatch.setattr(offload, "egress_is_reachable", lambda url, timeout=3.0: (True, ""))

    seen = {}
    monkeypatch.setattr(offload, "resolve_command", lambda agent="codebuddy": "/usr/bin/codebuddy")
    monkeypatch.setattr(
        offload.subprocess, "run",
        lambda argv, **kw: seen.update(env=kw["env"]) or _Completed(stdout=json.dumps({"result": "ok"})),
    )
    result = offload.run_task("go", cwd=workdir)

    assert seen["env"]["HTTPS_PROXY"] == "http://knob.invalid:9999"
    assert result.proxy_source == offload.PROXY_ENV


def test_unreadable_config_refuses_before_spawning(workdir, monkeypatch):
    """The other half of "unreadable means refuse" — and the half I had not tested.

    The existing test fakes ``load_table`` raising, which is a MALFORMED table.  The
    path that actually bites on this box is the config pipe failing (unresolvable
    home, unreadable file): Network's `load_table` deliberately swallows that so a
    spawn can still start, and marks the verdict in ``Table.unreadable`` instead.
    So this fakes ``plobi_cli.config.load_config_readonly`` — the real source — and
    asserts the offload still refuses to run unrouted.
    """
    import agent.egress.rules as egress_rules
    import plobi_cli.config as plobi_config

    calls: list = []
    monkeypatch.setattr(egress_rules, "_TABLE_CACHE", None, raising=False)
    monkeypatch.setattr(egress_rules, "_UNREADABLE_REPORTED", False, raising=False)
    monkeypatch.setattr(
        plobi_config, "load_config_readonly",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("config.yaml unreadable")),
    )
    monkeypatch.delenv(offload.PROXY_ENV, raising=False)
    monkeypatch.setattr(offload, "resolve_command", lambda agent="codebuddy": "/usr/bin/codebuddy")
    monkeypatch.setattr(
        offload.subprocess, "run",
        lambda *a, **k: calls.append(a) or _Completed(stdout="{}"),
    )
    result = offload.run_task("go", cwd=workdir)

    assert calls == [], "an unreadable config must not start a child with no routing"
    assert result.ok is False
    assert result.cost_status == "not_started"
    assert "could not be read" in result.error
    assert "RuntimeError" in result.error, "the report must carry the real reason"

    monkeypatch.setattr(egress_rules, "_TABLE_CACHE", None, raising=False)
    monkeypatch.setattr(egress_rules, "_UNREADABLE_REPORTED", False, raising=False)


def test_no_table_declared_is_not_the_same_as_unreadable(monkeypatch):
    """"The user declared nothing" must stay inert, not a refusal — otherwise this
    channel breaks for every install that has no ``network.egress`` yet, and the
    distinction Network built `Table.unreadable` for collapses back into one."""
    import agent.egress.rules as egress_rules
    from agent.egress.rules import parse_table

    table = parse_table({"proxies": {}, "rules": []})
    assert table.active is False and table.unreadable == ""
    monkeypatch.setattr(egress_rules, "load_table", lambda *a, **k: table)
    env = {"HTTPS_PROXY": "http://kept.invalid:1"}
    assert offload.project_egress_onto(env) == ""
    assert env["HTTPS_PROXY"] == "http://kept.invalid:1", "an empty table must not rewrite the env"


def test_unreadable_table_refuses_instead_of_running_unrouted(workdir, monkeypatch):
    """A table that fails to parse must not degrade into "no routing" — that would
    send the child out through whatever the environment happens to say."""
    import agent.egress.rules as egress_rules

    def boom(*a, **k):
        raise egress_rules.EgressConfigError("rule 1 has no catch-all")

    calls: list = []
    monkeypatch.setattr(egress_rules, "load_table", boom)
    monkeypatch.delenv(offload.PROXY_ENV, raising=False)
    # Pin the binary so the flow always reaches the egress-table parse: on CI
    # ``codebuddy`` is absent and ``resolve_command`` would fail the task
    # before ``load_table`` ever runs, turning a config error into "no route".
    monkeypatch.setattr(
        offload, "resolve_command", lambda agent="codebuddy": "/usr/bin/codebuddy"
    )
    monkeypatch.setattr(
        offload.subprocess, "run",
        lambda *a, **k: calls.append(a) or _Completed(stdout="{}"),
    )
    result = offload.run_task("go", cwd=workdir)

    assert calls == []
    assert result.ok is False
    assert "catch-all" in result.error


def test_default_workdir_does_not_land_on_the_temp_volume(monkeypatch, tmp_path):
    """Offload output is run data, and this box forbids run data on C
    (``D:\\_POLICY``).  ``PLOBI_HOME`` is the policy-compliant, profile-aware
    root, so the default landing spot must sit under it rather than under the
    OS temp dir.  Asserted as a relationship, not a literal path."""
    import tempfile
    from pathlib import Path as _P

    home = tmp_path / "plobi-home"
    monkeypatch.setenv("PLOBI_HOME", str(home))
    monkeypatch.setattr("plobi_constants.get_plobi_home", lambda: home)

    wd = offload.default_workdir()
    assert wd.is_dir()
    assert _P(str(wd)).is_relative_to(home), wd
    # Only the system temp volume itself is rejected: pytest's own temp root
    # legitimately sits under the OS temp dir (so the parent chain always
    # contains it), but the landing spot must never *be* the temp volume.
    # Compare resolved paths to dodge symlink noise.
    temp_root = _P(tempfile.gettempdir()).resolve()
    assert wd != temp_root, wd


def test_no_dev_time_proxy_address_is_hardcoded():
    """127.0.0.1:7890 is this box's Clash port; baking it breaks on another
    network or after a reinstall, and hides that no proxy was configured."""
    code = [
        line for line in Path(offload.__file__).read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    ]
    blob = " ".join(code)
    # Naming the port in a comment is fine; assigning it as a default is not.
    assert '"7890"' not in blob and ":7890" not in blob
    assert "://127.0.0.1" not in blob


def test_shared_resolver_is_reused_not_recopied():
    import inspect

    from agent import process_bootstrap

    assert hasattr(process_bootstrap, "_get_proxy_for_base_url")
    body = inspect.getsource(offload.resolve_proxy)
    assert "_get_proxy_for_base_url" in body
    # A second narrow reader would read those names off os.environ itself.
    for name in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "https_proxy"):
        assert f'os.environ.get("{name}"' not in body, name


def test_unreachable_network_is_named_and_never_a_silent_success(workdir, monkeypatch):
    monkeypatch.delenv(offload.PROXY_ENV, raising=False)
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.delenv("https_proxy", raising=False)
    monkeypatch.delenv("HTTP_PROXY", raising=False)
    monkeypatch.setattr(offload, "resolve_command", lambda agent="codebuddy": "/usr/bin/agy")
    monkeypatch.setattr(
        offload.subprocess, "run",
        lambda *a, **k: _Completed(
            returncode=1,
            stderr='error: Eligibility check failed: Get "https://www.googleapis.com/'
                   'oauth2/v2/userinfo": dial tcp 198.18.0.45:443: connectex: '
                   "A connection attempt failed",
        ),
    )
    result = offload.run_task("go", cwd=workdir)

    assert result.ok is False
    assert "network unreachable" in result.error
    assert offload.PROXY_ENV in result.error  # says what to set, not just that it died


def test_exit_zero_with_no_artifact_is_not_reported_as_success(workdir, monkeypatch):
    """Measured on this box: codebuddy returned exit 0 after 53s and 114k input
    tokens having written nothing, because its tools were disabled in print mode."""
    monkeypatch.setattr(offload, "resolve_command", lambda agent="codebuddy": "/usr/bin/codebuddy")
    monkeypatch.setattr(
        offload.subprocess, "run",
        lambda *a, **k: _Completed(stdout=json.dumps({"result": "I couldn't complete it"})),
    )
    result = offload.run_task("go", cwd=workdir)
    assert result.ok is False and result.produced is False
    assert "no artifact" in result.error


def test_product_entry_exists_and_reports_the_four_answers():
    r"""A D:\Temp script passing is not acceptance — the product call path must exist."""
    import agent.external_offload as mod

    assert callable(getattr(mod, "main", None))
    fields = set(offload.OffloadResult.__dataclass_fields__)
    assert {"agent", "cwd", "permission_flags", "usage", "cost", "cost_status"} <= fields

def test_dead_egress_is_named_before_anything_is_spent(workdir, monkeypatch):
    """A child must not get the chance to lie about why it failed.

    ``agy`` reports a dead proxy as "Please sign in to view available models" —
    measured on this box at 15:4x with the table pointing at a port nothing
    listens on. The channel has to say "the exit is not reachable" and spend zero.
    """
    calls: list = []
    monkeypatch.setenv(offload.PROXY_ENV, "http://127.0.0.1:1")
    monkeypatch.setattr(offload, "resolve_command", lambda agent="codebuddy": "/usr/bin/codebuddy")
    monkeypatch.setattr(
        offload.subprocess, "run",
        lambda *a, **k: calls.append(a) or _Completed(stdout="{}"),
    )
    result = offload.run_task("go", cwd=workdir)

    assert calls == [], "an unreachable exit must stop the run before the child starts"
    assert result.ok is False
    assert result.cost_status == "not_started"
    assert "not reachable" in result.error


def test_reachability_probe_uses_a_real_socket():
    """The probe is the guard, so it gets tested against a real closed port —
    bind one, release it, and the connect must be refused, not silently accepted."""
    import socket

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    ok, why = offload.egress_is_reachable(f"http://127.0.0.1:{port}", timeout=2.0)
    assert ok is False
    assert f"127.0.0.1:{port}" in why

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    try:
        live_port = listener.getsockname()[1]
        assert offload.egress_is_reachable(f"http://127.0.0.1:{live_port}", timeout=2.0) == (True, "")
    finally:
        listener.close()



# ── 回执口（WP-OFFLOAD-REPLY）：派活时给身份，它写文件，我们收 ──────────────

def _capture_run(seen, workdir):
    def fake_run(argv, **kwargs):
        seen["argv"] = list(argv)
        seen["env"] = kwargs["env"]
        return _Completed(stdout=json.dumps({"result": "ok", "usage": {}}))
    return fake_run


@pytest.fixture
def replies(tmp_path, monkeypatch):
    """把回执目录钉在 tmp——不许有测试往真家写文件。"""
    root = tmp_path / "replies"
    monkeypatch.setenv(offload.REPLY_DIR_ENV, str(root))
    return root


def test_the_reply_handle_is_a_file_named_by_this_runs_job(workdir, replies, monkeypatch):
    seen = {}
    monkeypatch.setattr(offload, "resolve_command", lambda agent="qoder": "/usr/bin/qodercli")
    monkeypatch.setattr(offload.subprocess, "run", _capture_run(seen, workdir))

    offload.run_task("把课表采完", cwd=workdir, agent="qoder", reply_to="Sindri")

    task = seen["argv"][seen["argv"].index("-p") + 1]
    target = seen["env"][offload.REPLY_FILE_ENV]
    assert "【回执】" in task
    assert target.endswith(".json") and replies.name in target
    # 任务书里那条路径与环境变量里那个必须是同一个值，否则它写到别处＝永远收不到
    assert target.replace("\\", "/") in task.replace("\\", "/")


def test_reply_written_by_the_external_agent_is_collected_once(workdir, replies, monkeypatch):
    """它写回来的东西要收进结果，并且**读一次就收走**（下一轮不许重复计数）。"""
    def fake_run(argv, **kwargs):
        path = Path(kwargs["env"][offload.REPLY_FILE_ENV])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"text": "暗号是：紫罗兰三号。", "status": "ok"}', encoding="utf-8")
        return _Completed(stdout=json.dumps({"result": "done", "usage": {}}))

    monkeypatch.setattr(offload, "resolve_command", lambda agent="qoder": "/usr/bin/qodercli")
    monkeypatch.setattr(offload.subprocess, "run", fake_run)

    result = offload.run_task("读 note.txt 回暗号", cwd=workdir, agent="qoder", reply_to="Sindri")

    assert result.reply == "暗号是：紫罗兰三号。"
    assert result.reply_status == "filed" and result.job and result.reply_to == "Sindri"
    assert list(replies.glob("*.json")) == [], "收走＝回执文件不再留在盘上"


def test_a_run_that_filed_nothing_says_not_filed_not_a_failure(workdir, replies, monkeypatch):
    """它没回话是一种**读数**，不许和"我们读坏了"混成一句，也不许因此把成功的活判失败。"""
    monkeypatch.setattr(offload, "resolve_command", lambda agent="qoder": "/usr/bin/qodercli")
    monkeypatch.setattr(offload.subprocess, "run", _capture_run({}, workdir))

    result = offload.run_task("干活", cwd=workdir, agent="qoder")

    assert result.reply == "" and result.reply_status == "not_filed"
    assert result.reply_to == "", "没人指名该向谁交代就留空，不硬编一个"


def test_a_read_only_qoder_run_is_not_judged_by_missing_artifacts(workdir, replies, monkeypatch):
    """「exit 0 但没落盘」那条实测判据不许套到没测过的第三家身上。

    10-22 真跑踩过：Qoder 读了文件、答对了内容，只因为没写文件就被判成"没干活"。
    """
    monkeypatch.setattr(offload, "resolve_command", lambda agent="qoder": "/usr/bin/qodercli")
    monkeypatch.setattr(offload.subprocess, "run", _capture_run({}, workdir))

    result = offload.run_task("只读，别改文件", cwd=workdir, agent="qoder")
    assert result.ok is True, "只读活没有产出是正常形状"

    monkeypatch.setattr(offload, "resolve_command", lambda agent="codebuddy": "/usr/bin/codebuddy")
    measured = offload.run_task("干活", cwd=workdir)
    assert measured.ok is False and "no artifact" in measured.error, "CodeBuddy 那条实测判据还在"


# ── 第三家进来之后：差异在表里，不在 if 梯子里 ─────────────────────────────

def test_each_family_keeps_its_own_mode_spellings():
    """CodeBuddy 的档名拼到 Qoder 上必须是**错**，不是"差不多能用"。

    这张表存在的理由就是这个：三家共用一条 `if` 梯子时，最可能出的事故是
    拿那家的档名去校这家，然后静默落到默认档＝一次没人察觉的全放开。
    """
    assert offload.permission_flags("codebuddy") == ("-y", "--permission-mode=bypassPermissions")
    assert offload.permission_flags("agy") == ("--dangerously-skip-permissions",)
    assert offload.permission_flags("qoder") == ("--permission-mode", "default")

    with pytest.raises(ValueError):
        offload.permission_flags("qoder", "bypassPermissions")   # 那是 CodeBuddy 的拼写
    with pytest.raises(ValueError):
        offload.permission_flags("codebuddy", "bypass_permissions")  # 那是 Qoder 的拼写


def test_qoder_default_is_the_conservative_mode(monkeypatch):
    """未实测过它的放开档行为 ⇒ 默认给最保守那一档，不拿"应该有"当"有"。"""
    monkeypatch.delenv(offload.QODER_PERMISSION_MODE_ENV, raising=False)
    assert offload.DEFAULT_QODER_PERMISSION_MODE == "default"
    monkeypatch.setenv(offload.QODER_PERMISSION_MODE_ENV, "bypass_permissions")
    assert offload.permission_flags("qoder") == ("--permission-mode", "bypass_permissions")


def test_qoder_child_does_not_inherit_the_sdk_entrypoint(workdir, monkeypatch):
    """Qoder Desktop 里派活时，那三个 SDK 变量必须洗掉，否则 CLI 直接拒普通调用。"""
    seen = {}
    for key in offload.QODER_SDK_ENV:
        monkeypatch.setenv(key, "set-by-the-desktop")
    monkeypatch.setattr(offload, "resolve_command", lambda agent="qoder": "/usr/bin/qodercli")
    monkeypatch.setattr(offload.subprocess, "run", _capture_run(seen, workdir))

    offload.run_task("看一眼 README 回一句", cwd=workdir, agent="qoder")

    assert [key for key in offload.QODER_SDK_ENV if key in seen["env"]] == []
    assert seen["argv"][0] == "/usr/bin/qodercli"
    assert "--permission-mode" in seen["argv"] and "default" in seen["argv"]


def test_other_families_still_inherit_the_environment(workdir, monkeypatch):
    """洗变量只针对 Qoder——CodeBuddy 那一族的现有行为不许被这张表顺手改掉。"""
    seen = {}
    monkeypatch.setenv(offload.QODER_SDK_ENV[0], "keep-me")
    monkeypatch.setattr(offload, "resolve_command", lambda agent="codebuddy": "/usr/bin/codebuddy")
    monkeypatch.setattr(offload.subprocess, "run", _capture_run(seen, workdir))

    offload.run_task("干活", cwd=workdir)

    assert seen["env"].get(offload.QODER_SDK_ENV[0]) == "keep-me"


def test_unknown_family_is_not_silently_treated_as_codebuddy():
    """表里没有的一家 ⇒ 认不出，不回落。回落＝用错旗标去起一个不存在的命令。"""
    assert offload.agent_spec("not-a-real-agent") is None
    assert offload.resolve_command("not-a-real-agent") == ""
    assert offload.permission_flags("not-a-real-agent") == ()


def test_qoder_options_go_before_the_prompt_and_codebuddy_stays_as_measured():
    """Qoder 的提示词是变长位置参数 `[query...]`：排在它后面的选项会被吞进提示词。

    10-22 实测踩过：`--output-format json` 与 `--permission-mode` 都排在提示词之后，
    结果回包是散文、写入被权限层当场拒。所以这一家必须把选项排在**前面**；
    CodeBuddy 那一族的形状是实测跑通的，一个字不许跟着变。
    """
    q = offload.build_argv("qoder", "/usr/bin/qodercli", "干活", ("--permission-mode", "default"), ())
    assert q.index("--output-format") < q.index("-p"), "选项必须在提示词之前"
    assert q.index("--permission-mode") < q.index("-p")
    assert q[-1] == "干活" and q[-2] == "-p"

    c = offload.build_argv("codebuddy", "/usr/bin/codebuddy", "干活", ("-y", "--permission-mode=bypassPermissions"), ())
    assert c == ("/usr/bin/codebuddy", "-p", "干活", "--output-format", "json",
                 "-y", "--permission-mode=bypassPermissions")
