"""外部 AI 软件会话的**通用原子工具**回归（11-04 用户拍的形状）。

全部离线：`send_for_agent` / `set_prefs_for_agent` / `sessions_for_agent` 一律注入假的，
不起真子进程、不写用户账本。判据只有一条——**能力位说不能做的，必须当场拒，
不许静默成功，也不许拿另一家的档名凑**。
"""

from __future__ import annotations

import json

import pytest

from plobi.delegation import external_capabilities as caps
from tools import offload_tools as ot


@pytest.fixture
def agent(monkeypatch):
    monkeypatch.setattr(ot, "_agent_identity", lambda: "Plobi")


# ── 单一真源 ──────────────────────────────────────────────────────────────

def test_the_six_vendors_are_the_only_truth_and_the_text_is_generated():
    assert caps.vendor_names() == ("qoder", "codebuddy", "marvis", "cursor", "agy", "opencode")
    assert caps.vendor_list_text() == "qoder / codebuddy / marvis / cursor / agy / opencode"


def test_qoder_and_opencode_can_speak_and_nobody_takes_attachments_yet():
    assert caps.supports("qoder", "send") is True
    # OpenCode：`opencode run --session <id>` 能续接那条会话（现读 `--help`）。
    assert caps.supports("opencode", "send") is True
    for vendor in ("codebuddy", "marvis", "cursor", "agy"):
        assert caps.supports(vendor, "send") is False
    assert all(not caps.supports(v, "attachments") for v in caps.vendor_names())
    # 编辑会话消息今天没有一家支持；语义没定之前别开这枚工具。
    assert all(not caps.supports(v, "edit") for v in caps.vendor_names())


def test_the_gate_names_the_vendor_and_lists_what_it_can_do():
    note = caps.unsupported_note("codebuddy", "send")

    assert "CodeBuddy" in note
    assert "send" in note
    assert "rename" in note

    unknown = caps.unsupported_note("not-a-vendor", "send")
    assert "不认识" in unknown


def test_permission_levels_are_normalised_per_vendor_not_guessed():
    assert caps.permission_alias("qoder", "full_auto") == "bypass_permissions"
    assert caps.permission_alias("codebuddy", "full_auto") == "bypassPermissions"
    assert caps.permission_alias("marvis", "full_auto") is None
    assert caps.permission_alias("qoder", "nonsense") is None


def test_every_schema_reads_the_same_vendor_list():
    """四张 schema 曾经各写一份名单（三家 / 五家 / 三家 / 三家），现在只认一份。"""
    schemas = [ot.SCAN_SCHEMA, ot.MARK_SCHEMA, ot.RENAME_SCHEMA, ot.ARCHIVE_SCHEMA,
               ot.DELETE_SCHEMA, ot.SEND_SCHEMA, ot.MODEL_SCHEMA, ot.PERMISSION_SCHEMA]
    blob = json.dumps(schemas, ensure_ascii=False)

    assert caps.vendor_list_text() in blob
    # 旧的手写名单不许再出现（大小写混着写、或只列三家那种）。
    for stale in ("qoder / codebuddy / marvis。", "Qoder / CodeBuddy / Marvis",
                  "Qoder / CodeBuddy / agy"):
        assert stale not in blob


def test_the_three_new_tools_are_registered_in_the_offload_toolset():
    from tools.registry import registry

    for name in ("external_session_send", "external_session_model", "external_session_permission"):
        entry = registry.get_entry(name)
        assert entry is not None
        assert entry.toolset == "offload"


# ── scan 带能力位 ──────────────────────────────────────────────────────────

def test_scan_puts_capabilities_on_every_row_and_every_source(monkeypatch, agent):
    import plobi.delegation.external_sessions as channel

    monkeypatch.setattr(
        channel,
        "sessions_for_agent",
        lambda _agent: {
            "sessions": [{"id": "s-1", "related": True, "vendor": "qoder"}],
            "sources": [{"vendor": "qoder", "readable": True, "sessions": 1, "sendable": True}],
        },
    )

    payload = json.loads(ot._handle_scan({}))

    assert "send" in payload["sessions"][0]["capabilities"]
    assert "send" in payload["sources"][0]["capabilities"]
    # 原有字段一个不能少——能力位是加上去的，不是换掉的。
    assert payload["sources"][0]["sendable"] is True
    assert payload["counts"]["related"] == 1


# ── send 闸门 ──────────────────────────────────────────────────────────────

def test_send_is_refused_for_a_read_only_vendor_without_spawning(monkeypatch, agent):
    called = []
    import plobi.delegation.external_sessions as channel

    monkeypatch.setattr(channel, "send_for_agent", lambda *a, **k: called.append(a))
    out = json.loads(ot._handle_send({"vendor": "codebuddy", "id": "s-1", "text": "hi"}))

    assert out["ok"] is False
    assert "send" in out["error"]
    assert called == []


def test_attachments_are_refused_loudly_instead_of_silently_dropped(monkeypatch, agent):
    called = []
    import plobi.delegation.external_sessions as channel

    monkeypatch.setattr(channel, "send_for_agent", lambda *a, **k: called.append(a))
    out = json.loads(ot._handle_send({
        "vendor": "qoder", "id": "s-1", "text": "看图", "attachments": ["D:/a.png"]
    }))

    assert out["ok"] is False
    assert "attachments" in out["error"]
    assert called == []


def test_send_reports_the_session_prefs_and_whether_they_were_applied(monkeypatch, agent):
    import plobi.delegation.external_sessions as channel

    monkeypatch.setattr(channel, "send_for_agent",
                        lambda *a, **k: {"ok": True, "data": {"reply": "ok"}})
    monkeypatch.setattr(channel, "prefs_for_agent",
                        lambda *a, **k: {"model": "qoder/qwen3.8-flash", "permission_level": "edit"})

    out = json.loads(ot._handle_send({"vendor": "qoder", "id": "s-1", "text": "hi"}))

    assert out["ok"] is True
    assert out["data"]["prefs"]["model"] == "qoder/qwen3.8-flash"
    # 今天没有一家能在续接时自动应用预设——必须照实报 false。
    assert out["data"]["prefs_applied"] is False


# ── 会话预设 ──────────────────────────────────────────────────────────────

def test_model_pref_is_written_without_pretending_to_change_the_other_app(monkeypatch, agent):
    import plobi.delegation.external_sessions as channel

    seen = {}
    monkeypatch.setattr(
        channel, "set_prefs_for_agent",
        lambda a, v, i, **kw: (seen.update(kw) or {"ok": True, "data": {"apply_on_send": False}}),
    )

    out = json.loads(ot._handle_model({"vendor": "qoder", "id": "s-1", "model": "qoder/qwen3.8-flash"}))

    assert seen == {"model": "qoder/qwen3.8-flash"}
    assert out["ok"] is True


def test_permission_pref_translates_the_normalised_level(monkeypatch, agent):
    import plobi.delegation.external_sessions as channel

    seen = {}
    monkeypatch.setattr(
        channel, "set_prefs_for_agent",
        lambda a, v, i, **kw: (seen.update(kw) or {"ok": True, "data": {}}),
    )

    out = json.loads(ot._handle_permission({"vendor": "qoder", "id": "s-1", "level": "full_auto"}))

    assert seen == {"permission_level": "full_auto"}
    assert out["data"]["vendor_flag"] == "bypass_permissions"


def test_an_unknown_permission_level_is_refused_before_writing(monkeypatch, agent):
    import plobi.delegation.external_sessions as channel

    called = []
    monkeypatch.setattr(channel, "set_prefs_for_agent", lambda *a, **k: called.append(a))
    out = json.loads(ot._handle_permission({"vendor": "qoder", "id": "s-1", "level": "god_mode"}))

    assert out["ok"] is False
    assert "read_only" in out["error"]
    assert called == []


# ── 真实账本往返（不 mock 写盘）──────────────────────────────────────────


def test_prefs_round_trip_through_the_real_index(tmp_path, monkeypatch):
    import plobi.delegation.external_sessions as channel

    monkeypatch.setattr(channel, "index_file", lambda: tmp_path / "index.json")
    monkeypatch.setattr(channel, "_in_agent_scope", lambda *a: True)
    monkeypatch.setattr(channel, "_locate", lambda v, i: {"vendor": v, "id": i, "project": "D:/x"})
    monkeypatch.setattr(channel, "_entry_for", lambda n: type("E", (), {"name": n})())

    out = channel.set_prefs_for_agent(
        "Plobi", "qoder", "s-1", model="qoder/qwen3.8-flash", permission_level="edit"
    )
    assert out["ok"] is True
    assert out["data"]["apply_on_send"] is False
    assert "还不能自动应用" in out["data"]["note"]

    assert channel.prefs_for_agent("Plobi", "qoder", "s-1") == {
        "model": "qoder/qwen3.8-flash", "permission_level": "edit"
    }

    channel.set_prefs_for_agent("Plobi", "qoder", "s-1", model="", permission_level="")
    assert channel.prefs_for_agent("Plobi", "qoder", "s-1") == {
        "model": None, "permission_level": None
    }


def test_a_vendor_that_is_not_in_the_roster_is_refused_before_touching_the_index(tmp_path, monkeypatch):
    import plobi.delegation.external_sessions as channel

    monkeypatch.setattr(channel, "index_file", lambda: tmp_path / "index.json")
    out = channel.set_prefs_for_agent("Plobi", "not-a-vendor", "s-1", model="x")

    assert out["ok"] is False
    assert not (tmp_path / "index.json").exists()
