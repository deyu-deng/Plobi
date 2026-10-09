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


def test_check_chatlog_deferred_when_no_listener(monkeypatch):
    """R-047: nothing listening is the postponed state, not a fault."""
    monkeypatch.setattr(D, "http_get_json", lambda url, timeout=2.5: (None, None))
    row = D.check_chatlog()
    assert row["color"] == D.DEFERRED
    assert "unreachable" in row["detail"]
    assert "R-047" in row["detail"]


def test_check_chatlog_stays_red_when_answered_wrongly(monkeypatch):
    """A service that is up and returning 503 is a real fault, so it stays red."""
    monkeypatch.setattr(D, "http_get_json", lambda url, timeout=2.5: (503, None))
    row = D.check_chatlog()
    assert row["color"] == D.RED


def test_check_aigw_is_not_green_for_the_gateway_demo_channel(monkeypatch):
    """这条钉死一个实测复现过的假绿：mock 通道也供 workbuddy/deepseek-chat。

    旧实现按 id 前缀判渠道，于是零真渠道的网关被报成绿——正是 R-048 说的「把没接
    画成好了」。判据必须是 provider 字段，跟 plobi.quota.gateway 派生用同一条。
    """
    monkeypatch.setattr(
        D,
        "http_get_json",
        lambda url, timeout=2.5, headers=None: (
            200,
            {"data": [{"id": "mock/echo", "provider": "mock"},
                     {"id": "workbuddy/deepseek-chat", "provider": "mock"}]},
        ),
    )
    row = D.check_aigw("http://127.0.0.1:8000/v1")
    assert row["color"] == D.YELLOW
    assert row["apps"] == []
    assert "no quota app wired" in row["detail"]


def test_check_aigw_green_only_when_a_real_provider_is_served(monkeypatch):
    monkeypatch.setattr(
        D,
        "http_get_json",
        lambda url, timeout=2.5, headers=None: (
            200,
            {"data": [{"id": "mock/echo", "provider": "mock"},
                     {"id": "workbuddy/glm-5.3", "provider": "workbuddy"}]},
        ),
    )
    row = D.check_aigw("http://127.0.0.1:8000/v1")
    assert row["color"] == D.GREEN
    assert row["apps"] == ["workbuddy"]


def test_check_aigw_does_not_guess_a_channel_from_the_id(monkeypatch):
    """没有 provider 字段的条目按「不是渠道」处理——宁可黄，不许靠 id 前缀猜绿。"""
    monkeypatch.setattr(
        D,
        "http_get_json",
        lambda url, timeout=2.5, headers=None: (200, {"data": [{"id": "workbuddy/x"}]}),
    )
    assert D.check_aigw("http://127.0.0.1:8000/v1")["color"] == D.YELLOW


def test_check_aigw_deferred_when_no_listener(monkeypatch):
    monkeypatch.setattr(D, "http_get_json", lambda url, timeout=2.5, headers=None: (None, None))
    row = D.check_aigw("http://127.0.0.1:8000/v1")
    assert row["color"] == D.DEFERRED
    assert "unreachable" in row["detail"]
    assert row["url"] == "http://127.0.0.1:8000/v1/models"


def test_check_aigw_stays_red_when_answered_wrongly(monkeypatch):
    monkeypatch.setattr(D, "http_get_json", lambda url, timeout=2.5, headers=None: (500, None))
    row = D.check_aigw("http://127.0.0.1:8000/v1")
    assert row["color"] == D.RED


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


def test_check_chatlog_config_deferred_when_never_set_up(tmp_path: Path):
    """Absent config == postponed (R-047); the collector was never turned on."""
    row = D.check_chatlog_config(tmp_path)
    assert row["color"] == D.DEFERRED
    assert "missing" in row["detail"]


def test_check_chatlog_config_stays_red_when_misconfigured(tmp_path: Path):
    """A config that exists but is the wrong mode means someone switched the
    collector on and it is broken — deferral must not swallow that."""
    cfg = tmp_path / "plobi" / "chatlog.json"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(json.dumps({"mode": "whitelist"}), encoding="utf-8")
    row = D.check_chatlog_config(tmp_path)
    assert row["color"] == D.RED


def test_check_whitelist_eff_deferred_when_never_swept(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("PLOBI_CHATLOG_CONFIG", raising=False)
    row = D.check_chatlog_whitelist_efficiency(tmp_path)
    assert row["color"] == D.DEFERRED
    assert "no sweep has ever run" in row["detail"]


def test_deferred_ids_all_have_an_enable_hint_and_no_windows_launcher():
    """Deferred rows must say how to switch the capability on *here*.

    The Windows box was decommissioned on 2026-09-27, so any hint pointing at a
    ``.ps1`` launcher is advice nobody on this machine can follow — that is how
    the old red rows lied.
    """
    assert D.DEFERRED_CAPABILITY_IDS == {"chatlog", "aigw", "collect", "whitelist_eff"}
    for check_id in D.DEFERRED_CAPABILITY_IDS:
        hint = D.DEFERRED_ENABLE_HINTS[check_id]
        assert hint.strip()
        assert ".ps1" not in hint
        assert D.deferred_row(check_id, "observed")["color"] == D.DEFERRED


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


def test_check_l1_secretary_form_green(tmp_path: Path):
    (tmp_path / "config.yaml").write_text(
        "plugins:\n  enabled:\n    - plobi-north-star\n"
        "toolsets:\n  - web\n  - clarify\n  - plobi_north_star\n"
        "platform_toolsets:\n  cli:\n    - plobi_north_star\n  gateway:\n    - clarify\n",
        encoding="utf-8",
    )
    (tmp_path / "SOUL.md").write_text(":::PLOBI_L1_ASK_ROUTING:::\n", encoding="utf-8")
    row = D.check_l1_secretary_form(tmp_path)
    assert row["color"] == D.GREEN
    assert row["plugin_enabled"] and row["narrow_toolsets"] and row["soul_fence"]


def test_check_l1_secretary_form_names_which_question_failed(tmp_path: Path):
    """三问三答：`terminal` 还在名单里就只能否掉窄名单那一条，另外两条照实说 yes。"""
    (tmp_path / "config.yaml").write_text(
        "plugins:\n  enabled:\n    - plobi-north-star\n"
        "toolsets:\n  - terminal\n  - plobi_north_star\n",
        encoding="utf-8",
    )
    (tmp_path / "SOUL.md").write_text(":::PLOBI_L1_ASK_ROUTING:::\n", encoding="utf-8")
    row = D.check_l1_secretary_form(tmp_path)
    assert row["color"] == D.RED
    assert "plugin=yes" in row["detail"]
    assert "narrow toolsets=NO" in row["detail"]
    assert "SOUL fence=yes" in row["detail"]
    assert "terminal" in row["detail"]


def test_check_l1_secretary_form_red_when_shape_never_applied(tmp_path: Path):
    row = D.check_l1_secretary_form(tmp_path)
    assert row["color"] == D.RED
    assert "never applied" in row["detail"]


def test_worst_exit():
    assert D.worst_exit([{"color": "yellow"}]) == 0
    assert D.worst_exit([{"color": "red"}]) == 1


def test_worst_exit_deferred_does_not_fail_the_run():
    """R-047: a fully healthy machine with the collection stack postponed must
    still exit 0. Red is the only failing colour."""
    rows = [
        {"color": D.GREEN},
        {"color": D.DEFERRED},
        {"color": D.DEFERRED},
        {"color": D.YELLOW},
    ]
    assert D.worst_exit(rows) == 0
    assert D.worst_exit(rows + [{"color": D.RED}]) == 1


def test_main_json(monkeypatch, capsys):
    monkeypatch.setattr(D, "run_checks", lambda send_test=False: [{"id": "x", "color": "green", "detail": "ok"}])
    assert D.main(["--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["checks"][0]["id"] == "x"


# ── WP-QUOTA-KEY-LIVENESS：cred_refs 与 quota_liveness 两行的判据 ─────────────────
# 判据形状（裁定 95 的最终形状）：**红只来自真请求回来的错误码**。下面特意有两条例外
# 反证 —— 拿不到监听者、以及 model 不在 /models 清单里 —— 都不许判红：这台实测过
# `/models` 会少报（glm-4-air / glm-5v-turbo 目录里没有而照样 200）。探针一律零生成。

SECRET_STANDIN = "sk-should-never-appear-in-a-row"


def _dotenv(monkeypatch, tmp_path: Path, text: str) -> Path:
    env = tmp_path / ".env"
    env.write_text(text, encoding="utf-8")
    monkeypatch.setattr(D, "_credential_env_file", lambda: env)
    return env


def test_credential_refs_red_when_reference_was_never_expanded(monkeypatch, tmp_path):
    _dotenv(monkeypatch, tmp_path, f"GLM_API_KEY=op://Vault/Item/field\nOTHER={SECRET_STANDIN}\n")
    monkeypatch.delenv("GLM_API_KEY", raising=False)

    row = D.check_credential_refs()

    assert row["color"] == D.RED
    assert "GLM_API_KEY" in row["detail"], "得点名是哪个键"
    assert "改法" in row["detail"], "红必须说清改哪一处，不许只报状态"
    assert SECRET_STANDIN not in json.dumps(row, ensure_ascii=False), "行里不许出现任何凭据值"
    assert "op://" in row["detail"] and "Vault" not in row["detail"], (
        "只报形态与键名，不把引用指向的库/条目结构抄进表里"
    )


def test_credential_refs_green_when_the_environment_has_the_expanded_value(monkeypatch, tmp_path):
    _dotenv(monkeypatch, tmp_path, "GLM_API_KEY=op://Vault/Item/field\n")
    monkeypatch.setenv("GLM_API_KEY", SECRET_STANDIN)

    row = D.check_credential_refs()

    assert row["color"] == D.GREEN
    assert "GLM_API_KEY" in row["detail"]
    assert SECRET_STANDIN not in json.dumps(row, ensure_ascii=False)


def test_credential_refs_copy_into_environ_is_not_resolution(monkeypatch, tmp_path):
    """apply_env_files() 会把未展开的引用原样灌进 os.environ —— 那不算"有解析值"。

    判反了就会把一条真故障读成绿：环境里那份和 .env 里那份是同一条引用。
    """
    _dotenv(monkeypatch, tmp_path, "GLM_API_KEY=op://Vault/Item/field\n")
    monkeypatch.setenv("GLM_API_KEY", "op://Vault/Item/field")

    assert D.check_credential_refs()["color"] == D.RED


def test_credential_refs_green_when_no_reference_at_all(monkeypatch, tmp_path):
    _dotenv(monkeypatch, tmp_path, f"GLM_API_KEY={SECRET_STANDIN}\n")

    row = D.check_credential_refs()

    assert row["color"] == D.GREEN
    assert SECRET_STANDIN not in json.dumps(row, ensure_ascii=False)


def _sources(monkeypatch, sources: dict) -> None:
    monkeypatch.setattr("plobi.quota.config.load", lambda: {"sources": sources, "order": list(sources)})


def test_quota_liveness_red_only_on_a_rejected_key(monkeypatch):
    _sources(monkeypatch, {"zhipu-air": {
        "kind": "cheap_api", "base_url": "https://example.invalid", "api_key": SECRET_STANDIN,
        "model": "glm-4-air", "key_env": "PLOBI_QUOTA_ZHIPU_KEY",
    }})
    monkeypatch.setattr(D, "_models_probe", lambda url, key, timeout=2.5: (401, None, '{"error":"invalid"}'))

    row = D.check_quota_liveness()

    assert row["color"] == D.RED
    assert "HTTP 401" in row["detail"]
    assert "改法" in row["detail"] and ".env" in row["detail"], "要指名 .env 优先、shell export 无效"
    assert SECRET_STANDIN not in json.dumps(row, ensure_ascii=False)


def test_quota_liveness_dead_model_name_is_red_because_it_came_back_as_an_error(monkeypatch):
    """这条是"红只来自真请求回来的错误码"的正例：400 + 上游的 Unknown Model。"""
    _sources(monkeypatch, {"zhipu-air": {
        "kind": "cheap_api", "base_url": "https://example.invalid/v1", "api_key": SECRET_STANDIN,
        "model": "glm-4-air",
    }})
    monkeypatch.setattr(
        D, "_models_probe",
        lambda url, key, timeout=2.5: (400, None, '{"error":{"code":"1211","message":"Unknown Model"}}'),
    )

    row = D.check_quota_liveness()

    assert row["color"] == D.RED
    assert "glm-4-air" in row["detail"] and "改法" in row["detail"]


def test_quota_liveness_no_listener_is_not_red(monkeypatch):
    _sources(monkeypatch, {"zhipu-air": {
        "kind": "cheap_api", "base_url": "https://example.invalid", "api_key": SECRET_STANDIN,
        "model": "glm-4-air",
    }})
    monkeypatch.setattr(D, "_models_probe", lambda url, key, timeout=2.5: (None, None, ""))

    row = D.check_quota_liveness()

    assert row["color"] == D.YELLOW, "拿不到监听者证明不了任何事 —— 判红就是假红"


def test_quota_liveness_absent_from_catalog_is_not_red(monkeypatch):
    """`/models` 少报是这台实测过的事实：目录里没有 ≠ 不服务。"""
    _sources(monkeypatch, {"zhipu-air": {
        "kind": "cheap_api", "base_url": "https://example.invalid", "api_key": SECRET_STANDIN,
        "model": "glm-4-air",
    }})
    monkeypatch.setattr(
        D, "_models_probe",
        lambda url, key, timeout=2.5: (200, {"data": [{"id": "glm-5.3-flash"}, {"id": "glm-5"}]}, ""),
    )

    row = D.check_quota_liveness()

    assert row["color"] == D.GREEN, "不许把「目录未列」升成红，也不许判成「缺这个模型」"
    assert "活着" in row["detail"] and "服务性未验" in row["detail"]


def test_quota_liveness_does_not_invent_evidence_for_derived_sources(monkeypatch):
    """aigw 派生源同 base_url 同 key：探一次只等于重探网关，不许造出"证明了单个 app"的行。"""
    _sources(monkeypatch, {
        "zhipu-air": {"kind": "cheap_api", "base_url": "https://example.invalid", "api_key": SECRET_STANDIN, "model": "glm-4-air"},
        "workbuddy": {"kind": "aigw", "base_url": "https://example.invalid", "api_key": SECRET_STANDIN, "model": "workbuddy/glm-5.3-flash"},
    })
    seen: list[str] = []

    def fake_probe(url, key, timeout=2.5):
        seen.append(url)
        return 200, {"data": [{"id": "glm-4-air"}]}, ""

    monkeypatch.setattr(D, "_models_probe", fake_probe)

    row = D.check_quota_liveness()

    assert len(seen) == 1, "派生源不该被单独探一次 —— 那是把网关那一腿重报一遍当新证据"
    assert "workbuddy" in row["detail"] and "真流量" in row["detail"], "要照实说这一腿证不了"
    assert row["color"] == D.GREEN


def test_worse_aggregates_red_over_yellow_and_never_downgrades():
    assert D._worse(D.GREEN, D.YELLOW) == D.YELLOW
    assert D._worse(D.YELLOW, D.RED) == D.RED
    assert D._worse(D.RED, D.YELLOW) == D.RED


def test_every_red_from_the_two_new_rows_names_a_fix(monkeypatch, tmp_path):
    """不变量（不是快照清单）：这两行只要判红，detail 里就得有改法。"""
    _dotenv(monkeypatch, tmp_path, "GLM_API_KEY=op://Vault/Item/field\n")
    monkeypatch.delenv("GLM_API_KEY", raising=False)
    _sources(monkeypatch, {"zhipu-air": {
        "kind": "cheap_api", "base_url": "https://example.invalid", "api_key": SECRET_STANDIN, "model": "glm-4-air",
    }})
    monkeypatch.setattr(D, "_models_probe", lambda url, key, timeout=2.5: (403, None, ""))

    for row in (D.check_credential_refs(), D.check_quota_liveness()):
        if row["color"] == D.RED:
            assert "改法" in row["detail"], f"{row['id']} 判红了却没说改哪一处"
