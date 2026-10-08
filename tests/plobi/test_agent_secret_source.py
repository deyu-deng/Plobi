"""WP-AGENT-SECRET-SOURCE — 注入的凭据要赢过分身自己家里那份旧拷贝。

裁定 42 §42.2 禁止分身家留第二份密钥，但早先建的家留了（实测 11 份同一个旧 key）。
凭据解析又是 ``.env`` 优先于进程环境，所以秘书把自己那把注进子进程也没用——子进程
读的是它那个家的旧拷贝，结果「L1 通、L2 全 401」。这一刀把顺序钉成一句可讲清的话：

    家目录共享凭据  <  这条记录声明的覆盖   （两者都算「注入」，都赢过分身家的 .env）

断言全是关系断言：只用 marker，不用真 key；也不靠网络。
"""

from __future__ import annotations

import pytest

from plobi.agents.registry import (
    AgentEntry,
    SHARED_SECRET_SUFFIXES,
    apply_secret_overrides,
    home_shared_secrets,
)
from plobi_cli.config import INJECTED_SECRETS_ENV, get_env_value_prefer_dotenv

HOME_SECRET = "marker-home-shared-key"
PROJECT_SECRET = "marker-project-override"
STALE_AGENT_COPY = "marker-stale-copy-in-agent-home"


@pytest.fixture
def fake_home_env(monkeypatch):
    """把「根家目录那份 .env」换成给定内容（签名跟 load_env 一致，可带路径参数）。"""

    def _set(entries: dict[str, str]):
        import plobi_cli.config as cfg

        monkeypatch.setattr(cfg, "load_env", lambda env_path=None: dict(entries))

    return _set


# ─── 注入登记：名字进环境变量，值不进 ─────────────────────────────────────


def test_registration_lists_names_only(fake_home_env):
    fake_home_env({"GLM_API_KEY": HOME_SECRET})
    env: dict[str, str] = {}
    apply_secret_overrides(env, AgentEntry(name="demo"))

    assert env[INJECTED_SECRETS_ENV] == "GLM_API_KEY"
    assert HOME_SECRET not in env[INJECTED_SECRETS_ENV]


def test_project_override_beats_the_home_shared_one(fake_home_env):
    fake_home_env({"GLM_API_KEY": HOME_SECRET})
    env: dict[str, str] = {}
    apply_secret_overrides(
        env, AgentEntry(name="demo", secret_overrides={"GLM_API_KEY": PROJECT_SECRET})
    )

    assert env["GLM_API_KEY"] == PROJECT_SECRET
    assert env[INJECTED_SECRETS_ENV] == "GLM_API_KEY"


def test_nothing_is_injected_when_there_is_no_credential(fake_home_env):
    fake_home_env({"PLOBI_LAN": "0", "SOME_FLAG": ""})
    env: dict[str, str] = {"PLOBI_LAN": "0"}
    apply_secret_overrides(env, AgentEntry(name="demo"))

    assert INJECTED_SECRETS_ENV not in env, "空覆盖不该造出一份登记"
    assert env == {"PLOBI_LAN": "0"}


def test_only_credential_shaped_names_are_shared(fake_home_env):
    fake_home_env({
        "GLM_API_KEY": HOME_SECRET,
        "OPENAI_API_KEY": "marker-openai",
        "ZHIPU_TOKEN": "marker-token",
        "PLOBI_LAN": "0",
        "model": "marker-not-a-secret",
    })
    shared = home_shared_secrets()

    assert set(shared) == {"GLM_API_KEY", "OPENAI_API_KEY", "ZHIPU_TOKEN"}
    assert all(name.endswith(SHARED_SECRET_SUFFIXES) for name in shared)


# ─── 解析顺序：登记过的赢，其余照旧 `.env` 优先 ────────────────────────────


def test_registered_injection_beats_the_agents_own_env(monkeypatch, fake_home_env):
    """这一条就是那格 401 的成因：分身家的旧拷贝原本会赢。"""
    fake_home_env({"GLM_API_KEY": HOME_SECRET})
    monkeypatch.setenv("GLM_API_KEY", HOME_SECRET)
    monkeypatch.setenv(INJECTED_SECRETS_ENV, "GLM_API_KEY")
    # 子进程那个家的 .env —— 分身早先被拷进去的旧 key
    import plobi_cli.config as cfg

    monkeypatch.setattr(cfg, "load_env", lambda env_path=None: {"GLM_API_KEY": STALE_AGENT_COPY})

    assert get_env_value_prefer_dotenv("GLM_API_KEY") == HOME_SECRET
    assert STALE_AGENT_COPY not in get_env_value_prefer_dotenv("GLM_API_KEY")


def test_unregistered_names_still_prefer_the_dotenv(monkeypatch, fake_home_env):
    """没登记的照旧：`.env` 赢过 shell 里遗留的旧 export（裁定 81 那句原话不改）。"""
    import plobi_cli.config as cfg

    fake_home_env({"GLM_API_KEY": HOME_SECRET})
    monkeypatch.delenv(INJECTED_SECRETS_ENV, raising=False)
    monkeypatch.setenv("GLM_API_KEY", "marker-stale-shell-export")

    assert get_env_value_prefer_dotenv("GLM_API_KEY") == HOME_SECRET


def test_registration_without_a_value_falls_back_to_the_dotenv(monkeypatch):
    """标记里写了名字但环境里没值 —— 不许因此把凭据判没，回落到原顺序。"""
    import plobi_cli.config as cfg

    monkeypatch.setattr(cfg, "load_env", lambda env_path=None: {"GLM_API_KEY": HOME_SECRET})
    monkeypatch.delenv("GLM_API_KEY", raising=False)
    monkeypatch.setenv(INJECTED_SECRETS_ENV, "GLM_API_KEY")

    assert get_env_value_prefer_dotenv("GLM_API_KEY") == HOME_SECRET


# ─── 裁定 89 甲：分身家的复印件不再作凭据来源 ──────────────────────────────


def _two_homes(tmp_path, monkeypatch, root_key: str, agent_key: str) -> "Path":
    """家目录 = tmp/root，分身家 = tmp/root/profiles/demo，各写一份不同的 key。"""
    from pathlib import Path

    import plobi_constants

    root = tmp_path / "root"
    agent = root / "profiles" / "demo"
    agent.mkdir(parents=True)
    (root / ".env").write_text(f"GLM_API_KEY={root_key}\n", encoding="utf-8")
    (agent / ".env").write_text(f"GLM_API_KEY={agent_key}\n", encoding="utf-8")
    monkeypatch.setenv("PLOBI_HOME", str(agent))
    monkeypatch.setattr(plobi_constants, "get_default_plobi_root", lambda: root)
    import plobi_cli.config as cfg

    cfg.invalidate_env_cache()
    cfg._mismatched_agent_copies_warned.clear()
    return agent


def test_agent_home_copy_is_not_a_credential_source(tmp_path, monkeypatch):
    """家目录那把赢；分身家那份复印件连读都不读——每条 spawn 路都自动如此。"""
    from pathlib import Path

    import plobi_cli.config as cfg

    agent = _two_homes(tmp_path, monkeypatch, HOME_SECRET, STALE_AGENT_COPY)
    monkeypatch.delenv("GLM_API_KEY", raising=False)
    monkeypatch.delenv(INJECTED_SECRETS_ENV, raising=False)

    assert cfg.credential_env_path() == (tmp_path / "root" / ".env")
    assert Path(cfg.get_env_path()) == agent / ".env", "分身家确实是被指向的那一份"
    assert get_env_value_prefer_dotenv("GLM_API_KEY") == HOME_SECRET


def test_the_ignored_copy_is_called_out(tmp_path, monkeypatch, caplog):
    """不读它，但要说出来：否则「我明明改了 key」下一轮照样没人看得出来。"""
    import logging

    _two_homes(tmp_path, monkeypatch, HOME_SECRET, STALE_AGENT_COPY)
    monkeypatch.delenv("GLM_API_KEY", raising=False)
    monkeypatch.delenv(INJECTED_SECRETS_ENV, raising=False)
    import plobi_cli.config as cfg

    cfg._mismatched_agent_copies_warned.clear()
    with caplog.at_level(logging.WARNING, logger="plobi_cli.config"):
        assert get_env_value_prefer_dotenv("GLM_API_KEY") == HOME_SECRET
        got = get_env_value_prefer_dotenv("GLM_API_KEY")  # 第二次不该再刷一遍

    text = " ".join(r.getMessage() for r in caplog.records)
    assert "GLM_API_KEY" in text and "ignored" in text
    assert HOME_SECRET not in text and STALE_AGENT_COPY not in text, "只许出现指纹，不许出现值"
    assert got == HOME_SECRET
    assert len([r for r in caplog.records if "ignored" in r.getMessage()]) == 1


def test_load_env_keeps_one_memo_slot_per_file(tmp_path, monkeypatch):
    """两个家交替读不能互相冲掉缓存（改之前是单格 memo）。"""
    import plobi_cli.config as cfg

    a, b = tmp_path / "a.env", tmp_path / "b.env"
    a.write_text("GLM_API_KEY=marker-a\n", encoding="utf-8")
    b.write_text("GLM_API_KEY=marker-b\n", encoding="utf-8")
    monkeypatch.setattr(cfg, "_env_cache", {})

    for _ in range(3):
        assert cfg.load_env(a)["GLM_API_KEY"] == "marker-a"
        assert cfg.load_env(b)["GLM_API_KEY"] == "marker-b"
    assert len(cfg._env_cache) == 2


# ─── 洞 B：额度桥不再自己抄一份顺序 ────────────────────────────────────────


def test_quota_bridge_uses_the_same_credential_entry(monkeypatch):
    """`.env` 里换的 key 与注入的 key 都要算数；这里不自己读 load_env。"""
    from plobi.quota import config as qcfg

    seen: list[str] = []

    def fake_resolver(name: str):
        seen.append(name)
        return {"PLOBI_QUOTA_ZHIPU_KEY": "marker-via-shared-entry"}.get(name, "")

    import plobi_cli.config as cfg

    monkeypatch.setattr(cfg, "get_env_value_prefer_dotenv", fake_resolver)
    monkeypatch.delenv("PLOBI_QUOTA_ZHIPU_KEY", raising=False)

    env = qcfg._env_with_credentials()
    assert env["PLOBI_QUOTA_ZHIPU_KEY"] == "marker-via-shared-entry"
    assert "PLOBI_QUOTA_ZHIPU_KEY" in seen

    merged = qcfg._resolve_source("zhipu-air", {}, env)
    assert merged["api_key"] == "marker-via-shared-entry"


# ─── 复印件不该落到盘上：这条是裁定 42 的原意，回归守住 ────────────────────


def test_injection_touches_no_files(tmp_path, fake_home_env):
    fake_home_env({"GLM_API_KEY": HOME_SECRET})
    profile_home = tmp_path / "profiles" / "demo"
    profile_home.mkdir(parents=True)
    (profile_home / ".env").write_text(f"GLM_API_KEY={STALE_AGENT_COPY}\n", encoding="utf-8")

    before = {p.name: p.read_bytes() for p in profile_home.iterdir()}
    env: dict[str, str] = {}
    apply_secret_overrides(env, AgentEntry(name="demo"))

    assert {p.name: p.read_bytes() for p in profile_home.iterdir()} == before
    assert env["GLM_API_KEY"] == HOME_SECRET
