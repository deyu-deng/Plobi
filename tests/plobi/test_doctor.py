"""Unit tests for scripts/plobi/doctor.py — network is mocked."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _load():
    path = REPO / "scripts" / "plobi" / "doctor.py"
    spec = importlib.util.spec_from_file_location("plobi_doctor", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


D = _load()


def test_parse_aigw_port_reads_server_block():
    text = "logging:\n  level: INFO\nserver:\n  host: 127.0.0.1\n  port: 9123\n"
    assert D.parse_aigw_port(text) == 9123


def test_parse_aigw_port_defaults():
    assert D.parse_aigw_port("foo: bar\n") == D.DEFAULT_AIGW_PORT


def test_check_chatlog_green(monkeypatch):
    monkeypatch.setattr(D, "http_get_json", lambda url, timeout=2.5: (200, {"ok": True}))
    row = D.check_chatlog()
    assert row["color"] == D.GREEN


def test_check_chatlog_red_when_down(monkeypatch):
    monkeypatch.setattr(D, "http_get_json", lambda url, timeout=2.5: (None, None))
    row = D.check_chatlog()
    assert row["color"] == D.RED
    assert "unreachable" in row["detail"]


def test_check_aigw_yellow_without_workbuddy(monkeypatch):
    monkeypatch.setattr(
        D,
        "http_get_json",
        lambda url, timeout=2.5, headers=None: (200, {"data": [{"id": "mock/echo"}]}),
    )
    row = D.check_aigw("http://127.0.0.1:8000/v1")
    assert row["color"] == D.YELLOW
    assert "workbuddy" in row["detail"]


def test_check_aigw_green_with_workbuddy(monkeypatch):
    monkeypatch.setattr(
        D,
        "http_get_json",
        lambda url, timeout=2.5, headers=None: (200, {"data": [{"id": "workbuddy/deepseek-chat"}]}),
    )
    row = D.check_aigw("http://127.0.0.1:8000/v1")
    assert row["color"] == D.GREEN


def test_load_env_file_does_not_need_quotes(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text("DINGTALK_WEBHOOK_URL=https://example.invalid/hook\n# comment\nFOO=bar\n", encoding="utf-8")
    parsed = D.load_env_file(env)
    assert parsed["DINGTALK_WEBHOOK_URL"].startswith("https://")
    assert parsed["FOO"] == "bar"


def test_apply_env_files_fills_missing(tmp_path: Path, monkeypatch):
    home_env = tmp_path / ".env"
    home_env.write_text("DINGTALK_WEBHOOK_URL=https://example.invalid/from-home\n", encoding="utf-8")
    monkeypatch.delenv("DINGTALK_WEBHOOK_URL", raising=False)
    D.apply_env_files(tmp_path)
    assert os.environ["DINGTALK_WEBHOOK_URL"].endswith("from-home")


def test_check_dingtalk_red_without_url(monkeypatch):
    monkeypatch.delenv("DINGTALK_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("DINGTALK_WEBHOOK_SECRET", raising=False)
    assert D.check_dingtalk()["color"] == D.RED


def test_check_dingtalk_green(monkeypatch):
    monkeypatch.setenv("DINGTALK_WEBHOOK_URL", "https://oapi.dingtalk.com/robot/send?access_token=x")
    monkeypatch.setenv("DINGTALK_WEBHOOK_SECRET", "s")
    assert D.check_dingtalk()["color"] == D.GREEN


def test_check_chatlog_config_blacklist(tmp_path: Path):
    cfg = tmp_path / "plobi" / "chatlog.json"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(json.dumps({"mode": "blacklist", "talkers": ["a"], "blacklist": []}), encoding="utf-8")
    row = D.check_chatlog_config(tmp_path)
    assert row["color"] == D.GREEN


def test_soul_has_routing_block_old_html_fence():
    assert D.soul_has_routing_block("<!-- PLOBI_L1_SECRETARY_ROUTING -->\n")


def test_soul_has_routing_block_new_colon_fence():
    assert D.soul_has_routing_block(":::PLOBI_L1_ASK_ROUTING:::\n")


def test_soul_has_routing_block_missing():
    assert not D.soul_has_routing_block("identity only, no routing fence\n")


def test_check_north_star_old_html_fence(tmp_path: Path):
    (tmp_path / "config.yaml").write_text(
        "toolsets:\n  - plobi_north_star\n", encoding="utf-8"
    )
    (tmp_path / "SOUL.md").write_text("<!-- PLOBI_L1_SECRETARY_ROUTING -->\n", encoding="utf-8")
    row = D.check_north_star(tmp_path)
    assert row["color"] == D.GREEN


def test_check_north_star_new_colon_fence(tmp_path: Path):
    (tmp_path / "config.yaml").write_text(
        "toolsets:\n  - plobi_north_star\n", encoding="utf-8"
    )
    (tmp_path / "SOUL.md").write_text(":::PLOBI_L1_ASK_ROUTING:::\n", encoding="utf-8")
    row = D.check_north_star(tmp_path)
    assert row["color"] == D.GREEN


def test_worst_exit():
    assert D.worst_exit([{"color": "yellow"}]) == 0
    assert D.worst_exit([{"color": "red"}]) == 1


def test_main_json(monkeypatch, capsys):
    monkeypatch.setattr(D, "run_checks", lambda send_test=False: [{"id": "x", "color": "green", "detail": "ok"}])
    assert D.main(["--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["checks"][0]["id"] == "x"
