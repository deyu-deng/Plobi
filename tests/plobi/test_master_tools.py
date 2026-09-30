"""B1: L1 Master 窄接口 — plobi_master_dispatch/status/preview/approve.

四个薄工具包装 plobi kanban（kanban_db.dispatch_once / promote_task /
specify_triage_task / board_stats），service-gated 注册：worker 上下文
（PLOBI_KANBAN_TASK）不可见，profile 需启用 ``plobi_north_star`` toolset。
L1 只看到这四个窄工具，不暴露原始 ``kanban_*`` 生命周期工具。
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from plobi_cli import kanban_db as kb


def _load_master_tools():
    """插件目录带连字符（plobi-north-star），不能按包名导入，按文件路径加载。

    仅加载 master_tools.py 本身（模块级只依赖标准库与 tools.registry），
    不加载插件 ``__init__.py``，避免 PluginContext 注册副作用。
    """
    path = (
        Path(__file__).resolve().parents[2]
        / "plugins" / "plobi-north-star" / "master_tools.py"
    )
    spec = importlib.util.spec_from_file_location("plobi_master_tools", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MT = _load_master_tools()


@pytest.fixture()
def board(monkeypatch, tmp_path):
    """Per-test kanban board under the redirected PLOBI_HOME.

    ``dispatch_once`` 校验 assignee 是否为真实 profile（测试环境无
    ``~/.plobi/profiles/*``），monkeypatch 视为全部存在，便于观察
    ``spawned`` 候选。

    ``run_tests.sh`` 用 ``env -i`` 起子进程（无 USERPROFILE），``Path.home()``
    会炸；沿用 tests/tools/test_kanban_tools.py 的做法 stub 掉。
    """
    from pathlib import Path as _Path

    monkeypatch.setattr(_Path, "home", lambda: tmp_path)
    kb.init_db()
    conn = kb.connect()
    monkeypatch.setattr("plobi_cli.profiles.profile_exists", lambda name: True)
    yield conn
    conn.close()


@pytest.fixture()
def master_mode(monkeypatch):
    """模拟 L1 profile：toolsets 启用 plobi_north_star。"""
    monkeypatch.setattr(
        "plobi_cli.config.load_config",
        lambda: {"toolsets": ["plobi_north_star"]},
    )


def _call(handler, **args):
    return json.loads(handler(args))


# ---------------------------------------------------------------------------
# service gate
# ---------------------------------------------------------------------------


def test_gate_off_without_toolset(monkeypatch):
    """默认 profile 未打开 plobi_north_star 时看不到工具。"""
    monkeypatch.setattr(
        "plobi_cli.config.load_config",
        lambda: {"toolsets": ["plobi-cli"]},
    )
    assert MT.check_plobi_master_mode() is False


def test_gate_on_with_toolset(master_mode):
    assert MT.check_plobi_master_mode() is True


def test_gate_on_default_profile_without_master(monkeypatch):
    """裁定 6：无 master 时，当前 default 打开 toolset 即可看见 secretary 工具。"""
    monkeypatch.setattr(
        "plobi_cli.config.load_config",
        lambda: {
            "toolsets": ["plobi-cli", "plobi_north_star"],
            "platform_toolsets": {
                "cli": ["plobi-cli", "plobi_north_star"],
                "gateway": ["plobi-cli", "plobi_north_star"],
            },
        },
    )
    assert MT.check_plobi_master_mode() is True


def test_gate_on_platform_toolsets_only(monkeypatch):
    monkeypatch.setattr(
        "plobi_cli.config.load_config",
        lambda: {
            "toolsets": ["plobi-cli"],
            "platform_toolsets": {"gateway": ["plobi_north_star"]},
        },
    )
    assert MT.check_plobi_master_mode() is True


def test_ensure_north_star_toolset_writes_default_config(monkeypatch):
    from plobi.agents.registry import L1_MID_TOOLSETS, ensure_north_star_toolset

    stored = {"toolsets": ["plobi-cli"]}
    saved: dict = {}
    monkeypatch.setattr("plobi_cli.config.load_config", lambda: stored)

    def fake_save(cfg, **_kwargs):
        saved.clear()
        saved.update(cfg)

    monkeypatch.setattr("plobi_cli.config.save_config", fake_save)
    monkeypatch.setattr(
        "plobi.agents.registry.ensure_l1_secretary_routing_soul",
        lambda **_kwargs: False,
    )
    ensure_north_star_toolset()
    assert saved["toolsets"] == list(L1_MID_TOOLSETS)
    assert "plobi-cli" not in saved["toolsets"]
    assert "terminal" not in saved["platform_toolsets"]["cli"]
    assert "session_search" not in saved["platform_toolsets"]["gateway"]
    assert "plobi_north_star" in saved["platform_toolsets"]["cli"]
    assert "clarify" in saved["platform_toolsets"]["gateway"]
    assert "plobi-north-star" in saved["plugins"]["enabled"]


def test_l1_mid_narrow_drops_terminal_and_session_search():
    from plobi.agents.registry import apply_l1_mid_toolsets, mid_narrow_toolset_names

    narrowed = mid_narrow_toolset_names(["plobi-cli", "spotify"])
    assert "terminal" not in narrowed
    assert "session_search" not in narrowed
    assert "code_execution" not in narrowed
    assert "plobi_north_star" in narrowed
    assert "clarify" in narrowed
    assert "spotify" in narrowed

    cfg = {
        "toolsets": ["plobi-cli"],
        "platform_toolsets": {
            "cli": ["plobi-cli", "plobi_north_star"],
            "gateway": ["terminal", "session_search", "clarify"],
        },
    }
    assert apply_l1_mid_toolsets(cfg) is True
    for platform in ("cli", "gateway"):
        names = cfg["platform_toolsets"][platform]
        assert "terminal" not in names
        assert "session_search" not in names
        assert "plobi_north_star" in names
        assert "clarify" in names


def test_l1_mid_narrow_preserves_already_mid_list():
    from plobi.agents.registry import L1_MID_TOOLSETS, mid_narrow_toolset_names

    mid = list(L1_MID_TOOLSETS)
    assert mid_narrow_toolset_names(mid) == mid


# ---------------------------------------------------------------------------
# L1 中收也要认「用户自己勾的」——复用 8e01e9a 的同一个判据
# （``_explicit_toolset_optins``），与 tests/plobi/test_l2_diet.py 成对。
# ---------------------------------------------------------------------------


def _simulate_ui_toggle(cfg: dict, platform: str, name: str) -> None:
    """Reproduce the exact write shape of ``_save_platform_tools`` — the helper
    behind both ``plobi tools`` and the desktop Toolsets panel
    (``PUT /api/tools/toolsets/...`` writes ``platform_toolsets.cli``): a
    *sorted* list for that ONE platform, and never the top-level ``toolsets``.
    """
    listed = set(cfg.setdefault("platform_toolsets", {}).get(platform, []))
    listed.add(name)
    cfg["platform_toolsets"][platform] = sorted(listed)


def _narrowed_l1_config() -> dict:
    """The resting shape ``apply_l1_mid_toolsets`` leaves on disk — measured on
    ``~/.plobi/config.yaml`` (主秘书) and ``~/.plobi/profiles/l2-agenda/config.yaml``:
    all three lists equal, no drop-set name anywhere."""
    from plobi.agents.registry import L1_MID_TOOLSETS

    mid = list(L1_MID_TOOLSETS)
    return {
        "toolsets": list(mid),
        "platform_toolsets": {"cli": list(mid), "gateway": list(mid)},
    }


def _snapshot(cfg: dict) -> dict:
    import copy

    return copy.deepcopy(cfg)


def test_l1_mid_narrow_still_narrows_a_fresh_profile_everywhere():
    """首落无回归：继承来的顶层带着 terminal / memory（``create_profile
    (clone_config=True)`` 就是这么三份一起写的），一次 pass 必须三处全剥掉。"""
    from plobi.agents.registry import apply_l1_mid_toolsets

    cfg = {
        "toolsets": ["terminal", "memory", "web", "skills"],
        "platform_toolsets": {
            "cli": ["terminal", "memory", "web", "skills"],
            "gateway": ["terminal", "memory", "web", "skills"],
        },
    }
    assert apply_l1_mid_toolsets(cfg) is True

    for names in (
        [cfg["toolsets"], cfg["platform_toolsets"]["cli"], cfg["platform_toolsets"]["gateway"]]
    ):
        assert "terminal" not in names
        assert "memory" not in names
        assert "web" in names
        assert "plobi_north_star" in names
    # 三份一致 = 策略写入的形状 → 第二次无事可做。
    after = _snapshot(cfg)
    assert apply_l1_mid_toolsets(cfg) is False
    assert cfg == after


def test_inherited_composite_top_level_is_never_read_as_an_opt_in():
    """顶层是 ``plobi-cli``（``DEFAULT_CONFIG`` 合并进来的继承形状）时**不认**
    opt-in：复合工具集不列举任何东西，「不在基线里」证明不了是人工勾的。
    这一份正是 ``plobi-cli`` profile 今天的行为，一个字都不放宽。"""
    from plobi.agents.registry import apply_l1_mid_toolsets

    cfg = {
        "toolsets": ["plobi-cli"],
        "platform_toolsets": {
            "cli": ["plobi-cli", "terminal"],
            "gateway": ["terminal", "session_search", "clarify"],
        },
    }
    assert apply_l1_mid_toolsets(cfg) is True
    for platform in ("cli", "gateway"):
        names = cfg["platform_toolsets"][platform]
        assert "terminal" not in names, platform
        assert "session_search" not in names, platform
        assert "plobi_north_star" in names, platform


def test_absent_top_level_toolsets_narrows_exactly_as_before():
    """顶层根本没写 ``toolsets``：没有可比较的基线 → 照旧全剥（不新增判据）。"""
    from plobi.agents.registry import apply_l1_mid_toolsets

    cfg = {"platform_toolsets": {"cli": ["terminal", "web"]}}
    assert apply_l1_mid_toolsets(cfg) is True
    assert "terminal" not in cfg["toolsets"]
    assert "terminal" not in cfg["platform_toolsets"]["cli"]
    assert "web" in cfg["platform_toolsets"]["cli"]


def test_user_enabled_toolset_survives_the_next_l1_mid_pass():
    """The toggle must stop being a lie: ``apply_l1_mid_toolsets`` re-runs at
    every plugin load (``ensure_north_star_toolset``), and the ``terminal`` the
    user switched on afterwards has to still be there.
    """
    from plobi.agents.registry import apply_l1_mid_toolsets

    cfg = _narrowed_l1_config()
    assert apply_l1_mid_toolsets(cfg) is False

    _simulate_ui_toggle(cfg, "cli", "terminal")
    apply_l1_mid_toolsets(cfg)

    assert "terminal" in cfg["platform_toolsets"]["cli"]
    # 顶层基线仍是策略写入的产物——opt-in 只覆盖那一个平台列表。
    assert "terminal" not in cfg["toolsets"]
    assert "terminal" not in cfg["platform_toolsets"]["gateway"]
    # 归位之后完全稳定（idempotent）。
    after = _snapshot(cfg)
    assert apply_l1_mid_toolsets(cfg) is False
    assert cfg == after


def test_user_enabled_toolset_survives_repeated_l1_passes_and_other_strips():
    """The opt-in is not a one-shot exemption: it survives any number of later
    passes, while the pass keeps policing names the profile actually inherited.
    """
    from plobi.agents.registry import apply_l1_mid_toolsets

    cfg = _narrowed_l1_config()

    _simulate_ui_toggle(cfg, "cli", "terminal")
    _simulate_ui_toggle(cfg, "gateway", "terminal")
    # 顶层重新出现 ``session_search`` = 这个 profile 继承/被改回来的，照剥；
    # 只出现在两个平台列表里的 terminal = 用户勾选，保留。
    cfg["toolsets"].append("session_search")
    for platform in ("cli", "gateway"):
        cfg["platform_toolsets"][platform].append("session_search")

    assert apply_l1_mid_toolsets(cfg) is True
    assert "session_search" not in cfg["toolsets"]
    for platform in ("cli", "gateway"):
        names = cfg["platform_toolsets"][platform]
        assert "terminal" in names, platform
        assert "session_search" not in names, platform
    # 再跑一遍无事可做 = idempotent。
    after = _snapshot(cfg)
    assert apply_l1_mid_toolsets(cfg) is False
    assert cfg == after


def test_plobi_composite_is_never_a_user_opt_in_for_l1_mid():
    """``plobi-*`` 复合工具集把 L1 变回工人，且面板根本写不出它
    （``_save_platform_tools`` 先丢掉 platform default toolsets），所以即便
    只出现在单个平台列表里也照剥。"""
    from plobi.agents.registry import L1_MID_TOOLSETS, apply_l1_mid_toolsets

    cfg = {
        "toolsets": list(L1_MID_TOOLSETS),
        "platform_toolsets": {"cli": ["plobi-cli", "web"]},
    }
    assert apply_l1_mid_toolsets(cfg) is True
    assert "plobi-cli" not in cfg["platform_toolsets"]["cli"]
    assert "web" in cfg["platform_toolsets"]["cli"]


def test_l1_mid_narrow_leaves_an_untouched_profile_untouched():
    """用户没碰过的干净 profile：返回 False，内容一个字节都不动。"""
    from plobi.agents.registry import apply_l1_mid_toolsets

    cfg = _narrowed_l1_config()
    cfg["platform_toolsets"]["cli"] = sorted(
        set(cfg["platform_toolsets"]["cli"]) | {"kanban", "tts", "vision"}
    )
    before = _snapshot(cfg)

    assert apply_l1_mid_toolsets(cfg) is False
    assert cfg == before


def test_ensure_north_star_toolset_keeps_ui_opt_in_across_restarts(tmp_path, monkeypatch):
    """E2E through the real plugin-load path: 主秘书 / 日程 each run
    ``ensure_north_star_toolset()`` at every start, so the toggle has to survive
    the *second* startup against a config file on disk.
    """
    import yaml

    from plobi.agents.registry import L1_MID_TOOLSETS, ensure_north_star_toolset

    cfg_path = tmp_path / "config.yaml"

    def _dump(cfg):
        cfg_path.write_text(
            yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True), encoding="utf-8"
        )

    _dump({"toolsets": list(L1_MID_TOOLSETS), "platform_toolsets": {"cli": list(L1_MID_TOOLSETS)}})
    monkeypatch.setattr(
        "plobi_cli.config.load_config",
        lambda: yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {},
    )
    monkeypatch.setattr("plobi_cli.config.save_config", lambda cfg, **_kw: _dump(cfg))
    monkeypatch.setattr(
        "plobi.agents.registry.ensure_l1_secretary_routing_soul", lambda **_kw: False
    )

    # start #1 — the profile is already mid-narrowed; only the plugin record lands.
    ensure_north_star_toolset()

    # user opens terminal in the desktop Toolsets panel (writes cli only)
    on_disk = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    _simulate_ui_toggle(on_disk, "cli", "terminal")
    _dump(on_disk)

    # start #2 and #3 — the re-apply used to revert the toggle.
    ensure_north_star_toolset()
    ensure_north_star_toolset()

    saved = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    assert "terminal" in saved["platform_toolsets"]["cli"]
    assert "terminal" not in saved["toolsets"]
    assert "plobi-north-star" in saved["plugins"]["enabled"]


def test_ensure_l2_agenda_toolsets_restores_terminal(tmp_path):
    import yaml

    from plobi.agents.registry import ensure_l2_agenda_toolsets

    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        yaml.safe_dump(
            {
                "platform_toolsets": {
                    "cli": ["web", "clarify", "plobi_north_star"],
                    "gateway": ["web", "clarify", "plobi_north_star"],
                },
                "toolsets": ["web", "clarify", "plobi_north_star"],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    assert ensure_l2_agenda_toolsets(tmp_path) is True
    loaded = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    assert "terminal" in loaded["platform_toolsets"]["cli"]
    assert "terminal" in loaded["platform_toolsets"]["gateway"]
    assert "terminal" in loaded["toolsets"]
    assert ensure_l2_agenda_toolsets(tmp_path) is False


def test_ensure_l2_agenda_keeps_plobi_cli(tmp_path):
    import yaml

    from plobi.agents.registry import ensure_l2_agenda_toolsets

    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        yaml.safe_dump(
            {"platform_toolsets": {"cli": ["plobi-cli"], "gateway": ["plobi-cli"]}},
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    assert ensure_l2_agenda_toolsets(tmp_path) is False
    loaded = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    assert loaded["platform_toolsets"]["cli"] == ["plobi-cli"]


def test_l1_secretary_soul_upsert(tmp_path):
    from plobi.agents.registry import (
        L1_SOUL_BEGIN,
        ensure_l1_secretary_routing_soul,
    )

    assert ensure_l1_secretary_routing_soul(home=tmp_path) is True
    text = (tmp_path / "SOUL.md").read_text(encoding="utf-8")
    assert L1_SOUL_BEGIN in text
    assert "PLOBI_L1" in text
    assert "plobi_secretary_ask" in text
    assert "session_search" in text
    ensure_l1_secretary_routing_soul(home=tmp_path)
    text2 = (tmp_path / "SOUL.md").read_text(encoding="utf-8")
    assert text2.count(L1_SOUL_BEGIN) == 1


def test_l1_soul_fence_avoids_html_comment_injection():
    from tools.threat_patterns import scan_for_threats
    from plobi.agents.registry import (
        L1_SOUL_BEGIN,
        L1_SOUL_BLOCK,
        L1_SOUL_END,
        L1_SOUL_LEGACY_BEGIN,
    )

    assert "<!--" not in L1_SOUL_BEGIN
    assert "secret" not in L1_SOUL_BEGIN.lower()
    assert "secret" not in L1_SOUL_END.lower()
    assert "PLOBI_L1" in L1_SOUL_BEGIN
    assert "html_comment_injection" not in scan_for_threats(L1_SOUL_BEGIN)
    assert "html_comment_injection" not in scan_for_threats(L1_SOUL_END)
    assert "html_comment_injection" not in scan_for_threats(L1_SOUL_BLOCK)
    assert "html_comment_injection" in scan_for_threats(L1_SOUL_LEGACY_BEGIN)


def test_l1_secretary_soul_replaces_legacy_html_fence(tmp_path):
    from plobi.agents.registry import (
        L1_SOUL_BEGIN,
        L1_SOUL_LEGACY_BEGIN,
        L1_SOUL_LEGACY_END,
        ensure_l1_secretary_routing_soul,
    )

    (tmp_path / "SOUL.md").write_text(
        "identity\n\n"
        f"{L1_SOUL_LEGACY_BEGIN}\n旧块\n{L1_SOUL_LEGACY_END}\n",
        encoding="utf-8",
    )
    assert ensure_l1_secretary_routing_soul(home=tmp_path) is True
    text = (tmp_path / "SOUL.md").read_text(encoding="utf-8")
    assert L1_SOUL_BEGIN in text
    assert L1_SOUL_LEGACY_BEGIN not in text
    assert "旧块" not in text
    assert text.count(L1_SOUL_BEGIN) == 1


# ---------------------------------------------------------------------------
# WP-L2-IDENTITY / 裁定 42 §6: L1 身份围栏只许盖在默认 profile；分身盖自己的
# ---------------------------------------------------------------------------


_STUB_SOUL = "You are Plobi Agent, an intelligent AI assistant created by Nous Research.\n"


def _use_profile(tmp_path, monkeypatch, name):
    """Point PLOBI_HOME at ``~/.plobi/profiles/<name>`` (real resolution path)."""
    home = tmp_path / (".plobi" if name == "default" else f".plobi/profiles/{name}")
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("PLOBI_HOME", str(home))
    return home


def _stub_toolset_config(monkeypatch):
    """Keep the config half of ensure_north_star_toolset off the real profile."""
    monkeypatch.setattr("plobi_cli.config.load_config", lambda: {"toolsets": ["plobi-cli"]})
    monkeypatch.setattr("plobi_cli.config.save_config", lambda *_a, **_k: None)


def _l2_entry(**overrides):
    """An ``AgentEntry`` built only from fields the registry already has."""
    from plobi.agents.registry import AgentEntry

    data = {
        "name": "aura",
        "role": "l2_project",
        "mind_subtree": "Vault/projects/Aura",
        "description": "本机生活助手",
        "display_name": "Aura",
        "project_path": "/Users/ciel/Projects/Aura",
    }
    data.update(overrides)
    return AgentEntry(**data)


def test_north_star_toolset_strips_cloned_l1_fence_on_project_profile(tmp_path, monkeypatch):
    """非 default profile 启用 north-star：不盖 L1，还要把 --clone 抄来的那份纠掉。

    模拟 ``profiles/l2-agenda/SOUL.md`` —— 它与正本 ``~/.plobi/SOUL.md`` 字节相同，
    所以日程 L2 现在自称「Plobi 总秘书」。
    """
    from plobi.agents.registry import (
        L1_SOUL_BEGIN,
        ensure_l1_secretary_routing_soul,
        ensure_north_star_toolset,
    )

    home = _use_profile(tmp_path, monkeypatch, "l2-agenda")
    _stub_toolset_config(monkeypatch)
    (home / "SOUL.md").write_text(_STUB_SOUL, encoding="utf-8")
    assert ensure_l1_secretary_routing_soul(home=home) is True  # the clone
    assert L1_SOUL_BEGIN in (home / "SOUL.md").read_text(encoding="utf-8")

    ensure_north_star_toolset()

    text = (home / "SOUL.md").read_text(encoding="utf-8")
    assert L1_SOUL_BEGIN not in text
    assert "Plobi 总秘书" not in text
    assert _STUB_SOUL.strip() in text  # 围栏之外的内容一个字都不丢


def test_north_star_toolset_does_not_create_l1_fence_on_project_profile(tmp_path, monkeypatch):
    """门禁也要挡住「新建」：没有 SOUL.md 的分身 profile 不能因为装插件长出一份。"""
    from plobi.agents.registry import ensure_north_star_toolset

    home = _use_profile(tmp_path, monkeypatch, "aura")
    _stub_toolset_config(monkeypatch)

    ensure_north_star_toolset()

    assert not (home / "SOUL.md").exists()


def test_north_star_toolset_still_stamps_default_profile(tmp_path, monkeypatch):
    """WP-L1-PROMPT-LIVE 防回归：default profile 照旧拿到 L1 合同。"""
    from plobi.agents.registry import L1_SOUL_BEGIN, ensure_north_star_toolset

    home = _use_profile(tmp_path, monkeypatch, "default")
    _stub_toolset_config(monkeypatch)
    (home / "SOUL.md").write_text(_STUB_SOUL, encoding="utf-8")

    ensure_north_star_toolset()

    text = (home / "SOUL.md").read_text(encoding="utf-8")
    assert L1_SOUL_BEGIN in text
    assert "Plobi 总秘书" in text
    assert text.count(L1_SOUL_BEGIN) == 1


def test_l2_identity_soul_upsert_is_idempotent_and_self_naming(tmp_path):
    from plobi.agents.registry import (
        L2_SOUL_BEGIN,
        L2_SOUL_END,
        ensure_l2_identity_soul,
    )

    entry = _l2_entry()
    assert ensure_l2_identity_soul(entry, home=tmp_path) is True
    text = (tmp_path / "SOUL.md").read_text(encoding="utf-8")
    assert L2_SOUL_BEGIN in text
    assert "Aura" in text
    assert "/Users/ciel/Projects/Aura" in text
    assert "Vault/projects/Aura" in text
    assert "本机 Plobi 总秘书（L1）" in text
    assert "不要自称总秘书" in text
    assert "共享账本" in text

    assert ensure_l2_identity_soul(entry, home=tmp_path) is False
    again = (tmp_path / "SOUL.md").read_text(encoding="utf-8")
    assert again == text
    assert again.count(L2_SOUL_BEGIN) == 1
    assert again.count(L2_SOUL_END) == 1


def test_l2_identity_soul_uses_slug_when_no_display_name(tmp_path):
    from plobi.agents.registry import ensure_l2_identity_soul

    assert ensure_l2_identity_soul(
        _l2_entry(display_name="", project_path="", mind_subtree=""), home=tmp_path
    ) is True
    text = (tmp_path / "SOUL.md").read_text(encoding="utf-8")
    assert "**aura**" in text
    assert "Vault/projects/Aura" not in text


# --------------------------------------------------------------------------- #
# WP-L2-IDENTITY 第二刀（裁定 45 / 裁定 42 §42.2 第 6 项 / 裁定 44）：
# 身份段要像个「人」——不念内部字段值、不拿占位描述当用途、不盖掉人自己写的设定。
# --------------------------------------------------------------------------- #


def test_l2_identity_block_carries_no_internal_field_vocabulary():
    """内部字段值不进提示词：模型会原样念给用户听（裁定 45）。"""
    from plobi.agents.registry import L2_PLACEHOLDER_DESCRIPTION_PREFIX, build_l2_identity_soul_block

    entry = _l2_entry(description=f"{L2_PLACEHOLDER_DESCRIPTION_PREFIX}Aura")
    block = build_l2_identity_soul_block(entry)

    assert "l2_project" not in block
    assert "role " not in block
    assert "id `" not in block
    assert f"`{entry.name}`" not in block  # 反引号包起来的 slug
    assert "`l2_project`" not in block
    assert entry.role not in block
    # 真路径 / Mind 子树不是字段名，用户自己也这么说话——留着。
    assert "/Users/ciel/Projects/Aura" in block
    assert "Vault/projects/Aura" in block
    # L1 / L2 是用户自己的说法，围栏标记也依赖它。
    assert "L1" in block


def test_l2_placeholder_description_is_not_rendered_as_the_purpose():
    """注册表自动填的那句占位话不是「这个项目在做什么」——不许当用途念。"""
    import inspect

    from plobi.agents.registry import (
        L2_PLACEHOLDER_DESCRIPTION_PREFIX,
        _is_placeholder_project_description,
        build_l2_identity_soul_block,
        ensure_mind_project_agents,
    )

    stub = f"{L2_PLACEHOLDER_DESCRIPTION_PREFIX}Aura"
    assert _is_placeholder_project_description(stub) is True
    # ``ensure_mind_project_agents`` 在 slug 与目录名不一致时还会缀一句 raw 名。
    assert _is_placeholder_project_description(f"{stub} (raw dir name:  Aura! )") is True
    assert _is_placeholder_project_description("本机生活助手") is False
    assert _is_placeholder_project_description("") is False

    # 占位句只有 ``ensure_mind_project_agents`` 里那一份真源：它必须用常量拼，
    # 不许在渲染端再抄一遍字面量（抄一遍就会漂）。
    src = inspect.getsource(ensure_mind_project_agents)
    assert "L2_PLACEHOLDER_DESCRIPTION_PREFIX" in src
    assert '"l2_project for Mind project' not in src

    block = build_l2_identity_soul_block(_l2_entry(description=stub))
    assert "l2_project" not in block
    assert "这个项目在做的事" not in block
    assert "目前没有人写下来" in block

    real = build_l2_identity_soul_block(_l2_entry())
    assert "这个项目在做的事：本机生活助手" in real
    assert "目前没有人写下来" not in real


def test_l2_identity_block_reads_as_a_person_not_a_job_description():
    """观测到的 bug：分身把共享账本里别人的分工当成自己的身份。"""
    from plobi.agents.registry import build_l2_identity_soul_block

    block = build_l2_identity_soul_block(_l2_entry())

    assert "职责不是从共享记忆里挑一个来当" in block
    assert "架构 + 验证 + 文档 + 测试 + 小修" in block  # 举的就是总秘书那套
    assert "「我是谁」不看账本，看这份文件" in block
    assert "同一时刻只有一条现在" in block  # 裁定 44：自己那条「现在」
    assert "你在忙什么" in block
    assert len(block.splitlines()) <= 15


def test_l2_identity_soul_keeps_handwritten_persona_and_stays_idempotent(tmp_path):
    """围栏之外是人自己写的（裁定 42 §42.2 第 6 项）——重新生成不许盖掉。"""
    from plobi.agents.registry import (
        L1_SOUL_BEGIN,
        L2_SOUL_BEGIN,
        L2_SOUL_END,
        ensure_l2_identity_soul,
    )

    persona = "- 说话短，先给结论，偶尔损人一句。"
    stale = f"{L2_SOUL_BEGIN}\n## 旧版身份段\n- 过期的一行。\n{L2_SOUL_END}\n"
    (tmp_path / "SOUL.md").write_text(
        _STUB_SOUL + "\n" + persona + "\n" + stale, encoding="utf-8"
    )

    assert ensure_l2_identity_soul(_l2_entry(), home=tmp_path) is True
    text = (tmp_path / "SOUL.md").read_text(encoding="utf-8")

    assert persona in text  # 人设原样留着
    assert "过期的一行" not in text  # 只剥自己那道围栏
    assert text.count(L2_SOUL_BEGIN) == 1
    assert text.count(L2_SOUL_END) == 1
    assert L1_SOUL_BEGIN not in text
    assert "那才算你自己" in text  # 承认人设已经在了
    assert "那里还空着" not in text

    assert ensure_l2_identity_soul(_l2_entry(), home=tmp_path) is False
    assert (tmp_path / "SOUL.md").read_text(encoding="utf-8") == text


def test_l2_identity_soul_does_not_mistake_upstream_stub_for_a_persona(tmp_path):
    """出厂那段 512 B 样板不是人设——14 个分身围栏外目前只有它。"""
    from plobi.agents.registry import _soul_persona_text, ensure_l2_identity_soul

    (tmp_path / "SOUL.md").write_text(_STUB_SOUL, encoding="utf-8")
    assert _soul_persona_text(_STUB_SOUL) == ""

    assert ensure_l2_identity_soul(_l2_entry(), home=tmp_path) is True
    text = (tmp_path / "SOUL.md").read_text(encoding="utf-8")
    assert "那里还空着" in text
    assert "那才算你自己" not in text
    # 上游样板一个字都没动。
    assert _STUB_SOUL.strip() in text

    from plobi.agents.registry import L2_SOUL_BEGIN, L2_SOUL_END

    after = _soul_persona_text(text)
    assert after == ""
    assert text.count(L2_SOUL_BEGIN) == text.count(L2_SOUL_END) == 1


def test_l2_identity_soul_replaces_cloned_l1_fence(tmp_path):
    """同一文件里两段合同不许共存——L2 落笔即纠 L1 克隆。"""
    from plobi.agents.registry import (
        L1_SOUL_BEGIN,
        L2_SOUL_BEGIN,
        ensure_l1_secretary_routing_soul,
        ensure_l2_identity_soul,
    )

    (tmp_path / "SOUL.md").write_text(_STUB_SOUL, encoding="utf-8")
    assert ensure_l1_secretary_routing_soul(home=tmp_path) is True
    assert ensure_l2_identity_soul(_l2_entry(), home=tmp_path) is True

    text = (tmp_path / "SOUL.md").read_text(encoding="utf-8")
    assert L2_SOUL_BEGIN in text
    assert L1_SOUL_BEGIN not in text
    assert "WP-L1-IDENTITY" not in text


def test_l1_soul_upsert_drops_l2_fence(tmp_path):
    """反向也一样：L1 落笔即纠 L2 身份段。"""
    from plobi.agents.registry import (
        L1_SOUL_BEGIN,
        L2_SOUL_BEGIN,
        ensure_l1_secretary_routing_soul,
        ensure_l2_identity_soul,
    )

    assert ensure_l2_identity_soul(_l2_entry(), home=tmp_path) is True
    assert ensure_l1_secretary_routing_soul(home=tmp_path) is True

    text = (tmp_path / "SOUL.md").read_text(encoding="utf-8")
    assert L1_SOUL_BEGIN in text
    assert L2_SOUL_BEGIN not in text


def test_l2_identity_soul_never_written_on_default_profile(tmp_path):
    """default profile 是 L1 的座位——分身身份段永不落笔。"""
    from plobi.agents.registry import (
        L2_SOUL_BEGIN,
        ensure_l2_identity_soul,
    )

    assert ensure_l2_identity_soul(_l2_entry(profile="default"), home=tmp_path) is False
    assert ensure_l2_identity_soul(
        _l2_entry(role="l1_secretary", name="secretary"), home=tmp_path
    ) is False
    assert not (tmp_path / "SOUL.md").exists() or L2_SOUL_BEGIN not in (
        tmp_path / "SOUL.md"
    ).read_text(encoding="utf-8")


def test_l2_identity_soul_skips_unmaterialized_profile(tmp_path):
    """profile 目录还没落地 → 不动磁盘（不许为一份 SOUL 凭空建 profile）。"""
    from plobi.agents.registry import ensure_l2_identity_soul

    missing = tmp_path / "profiles" / "aura"
    assert ensure_l2_identity_soul(_l2_entry(), home=missing) is False
    assert not missing.exists()


def test_l2_identity_fence_avoids_html_comment_injection():
    """围栏形状照 L1：冒号围栏，不带 HTML 注释（威胁扫描会拦）。"""
    from tools.threat_patterns import scan_for_threats

    from plobi.agents.registry import (
        L2_SOUL_BEGIN,
        L2_SOUL_END,
        build_l2_identity_soul_block,
    )

    block = build_l2_identity_soul_block(_l2_entry())
    assert "<!--" not in L2_SOUL_BEGIN
    assert "secret" not in L2_SOUL_BEGIN.lower()
    assert "secret" not in L2_SOUL_END.lower()
    assert "PLOBI_L2" in L2_SOUL_BEGIN
    assert "html_comment_injection" not in scan_for_threats(L2_SOUL_BEGIN)
    assert "html_comment_injection" not in scan_for_threats(L2_SOUL_END)
    assert "html_comment_injection" not in scan_for_threats(block)
    assert "html_comment_injection" in scan_for_threats(
        "<!-- PLOBI_L2_SECRETARY_ROUTING -->"
    )
    # 身份段是 SOUL.md 里的一段，不是整篇——行数必须小（L1 那份是长篇合同）。
    assert len(block.splitlines()) <= 15


def test_secretary_ask_schema_steers_soft_routing():
    desc = MT.SECRETARY_ASK_SCHEMA["description"]
    assert MT.SECRETARY_ASK_SCHEMA["name"] == "plobi_secretary_ask"
    assert "session_search" in desc
    assert "terminal" in desc
    assert "agent id" in desc.lower()


def test_gate_hides_from_worker(master_mode, monkeypatch):
    """dispatcher-spawned worker 即使启用 toolset 也不可见。"""
    monkeypatch.setenv("PLOBI_KANBAN_TASK", "t_abc")
    assert MT.check_plobi_master_mode() is False


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def test_status_reports_board_counts(board):
    kb.create_task(board, title="待批任务", triage=True)
    kb.create_task(board, title="可派工", assignee="l2_agenda")

    out = _call(MT.handle_master_status)
    assert out["ok"] is True
    assert out["by_status"].get("triage") == 1
    assert out["by_status"].get("ready") == 1
    assert [t["title"] for t in out["awaiting_human"]] == ["待批任务"]
    assert [t["title"] for t in out["ready"]] == ["可派工"]


# ---------------------------------------------------------------------------
# preview（dry-run，不落盘）
# ---------------------------------------------------------------------------


def test_preview_is_dry_run(board):
    tid = kb.create_task(board, title="派工预览", assignee="l2_agenda")

    out = _call(MT.handle_master_preview)
    assert out["ok"] is True
    assert out["dry_run"] is True
    assert (tid, "l2_agenda", "") in [tuple(s) for s in out["spawned"]]
    # 任务仍是 ready，未被 claim
    assert kb.get_task(board, tid).status == "ready"


# ---------------------------------------------------------------------------
# dispatch
# ---------------------------------------------------------------------------


def test_dispatch_dry_run_matches_preview(board):
    tid = kb.create_task(board, title="实派", assignee="l2_agenda")

    out = _call(MT.handle_master_dispatch, dry_run=True)
    assert out["ok"] is True
    assert out["dry_run"] is True
    assert (tid, "l2_agenda", "") in [tuple(s) for s in out["spawned"]]
    assert kb.get_task(board, tid).status == "ready"


# ---------------------------------------------------------------------------
# approve
# ---------------------------------------------------------------------------


def test_approve_promotes_triage_to_ready(board):
    tid = kb.create_task(board, title="批准链", triage=True)

    out = _call(MT.handle_master_approve, task_id=tid, reason="L1 批准")
    assert out["ok"] is True
    assert out["status_before"] == "triage"
    assert out["status_after"] == "ready"
    assert kb.get_task(board, tid).status == "ready"


def test_approve_promotes_blocked_to_ready(board):
    tid = kb.create_task(board, title="解阻塞", initial_status="blocked")

    out = _call(MT.handle_master_approve, task_id=tid)
    assert out["ok"] is True
    assert out["status_after"] == "ready"


def test_approve_refuses_running(board):
    tid = kb.create_task(board, title="运行中")
    # create_task 无父任务一律落 ready；running 是 dispatcher claim 后的
    # 状态，直接写库构造。
    with kb.write_txn(board):
        board.execute("UPDATE tasks SET status = 'running' WHERE id = ?", (tid,))

    out = _call(MT.handle_master_approve, task_id=tid)
    assert out["ok"] is False
    assert "approve only applies" in out.get("error", "")
    assert kb.get_task(board, tid).status == "running"


def test_approve_requires_task_id(board):
    out = _call(MT.handle_master_approve)
    assert out["ok"] is False
    assert "task_id" in out.get("error", "")


def test_approve_unknown_task(board):
    out = _call(MT.handle_master_approve, task_id="t_does_not_exist")
    assert out["ok"] is False
    assert "not found" in out.get("error", "")


# ---------------------------------------------------------------------------
# 假模型 e2e 闭环：派工 → 状态 → 批准
# ---------------------------------------------------------------------------


def test_e2e_fake_model_loop(board, master_mode):
    """L1 秘书按工具调用顺序走完整闭环。

    ops 采集 → 任务进 triage → status 看到 awaiting_human → preview 确认
    无可派工 → approve 批准到 ready（带 assignee）→ dispatch 派工（dry_run
    观察 spawned）→ status 确认 ready 队列。
    """
    # 1. 上游产生待批准任务（triage）
    tid = kb.create_task(board, title="整理周报", triage=True)

    # 2. status：L1 看到 awaiting_human
    s1 = _call(MT.handle_master_status)
    assert tid in [t["id"] for t in s1["awaiting_human"]]

    # 3. preview：还没有 ready 可派工
    p1 = _call(MT.handle_master_preview)
    assert p1["spawned"] == []

    # 4. approve：L1 批准，任务 triage → ready 并带 assignee
    a = _call(
        MT.handle_master_approve,
        task_id=tid,
        assignee="l2_agenda",
        reason="e2e 闭环",
    )
    assert a["ok"] is True
    assert a["status_before"] == "triage"
    assert a["status_after"] == "ready"

    # 5. dispatch：派工候选出现
    d = _call(MT.handle_master_dispatch, dry_run=True)
    assert (tid, "l2_agenda", "") in [tuple(s) for s in d["spawned"]]

    # 6. status：ready 队列确认闭环
    s2 = _call(MT.handle_master_status)
    assert tid in [t["id"] for t in s2["ready"]]
    assert s2["by_status"].get("triage", 0) == 0


# ---------------------------------------------------------------------------
# WP-BE-5: plobi_secretary_ask — agenda refresh (not kanban)
# ---------------------------------------------------------------------------

from datetime import datetime, timedelta

from plobi.agenda.service import AgendaService
from plobi.agents.registry import (
    AgentRegistry,
    find_agenda_agent,
    run_secretary_ask,
)
from plobi.collectors.chatlog.client import ChatlogUnavailable
from plobi.collectors.chatlog.config import CollectorConfig
from plobi.collectors.chatlog.confirm import HeuristicConfirmer
from plobi.collectors.chatlog.pipeline import (
    ChatlogDead,
    ChatlogPipeline,
    refresh_agenda,
)
from plobi.collectors.chatlog.state import SeenStore, TalkerStore


class _FakeChatlog:
    """Stand-in for ChatlogClient. ``fail=True`` → unhealthy."""

    def __init__(self, *, fail: bool = False, by_talker: dict | None = None):
        self.fail = fail
        self.by_talker = by_talker or {}
        self.health_checks = 0

    def healthy(self) -> bool:
        self.health_checks += 1
        return not self.fail

    def fetch(self, talker: str, day=None):
        if self.fail:
            raise ChatlogUnavailable("boom")
        return self.by_talker.get(talker, [])

    def list_talkers(self, keyword: str = "", limit: int = 10000) -> list[str]:
        if self.fail:
            raise ChatlogUnavailable("boom")
        return list(self.by_talker)


def _tomorrow_iso(hour: int = 9) -> str:
    day = datetime.now().date() + timedelta(days=1)
    return datetime.combine(day, datetime.min.time()).replace(hour=hour).isoformat()


@pytest.fixture()
def secretary_home(tmp_path, monkeypatch):
    """Isolate PLOBI_HOME so spawn / registry never touch the real home."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    plobi = tmp_path / ".plobi"
    plobi.mkdir()
    monkeypatch.setenv("PLOBI_HOME", str(plobi))
    monkeypatch.setenv("PLOBI_PROJECTS_CONFIG", str(plobi / "plobi" / "projects.yaml"))
    monkeypatch.delenv("PLOBI_MODELS_CONFIG", raising=False)
    return tmp_path


@pytest.fixture()
def stub_spawn(secretary_home, monkeypatch):
    """Shrink create_profile to mkdir so tests don't clone a real profile."""
    import plobi_cli.profiles as profiles_mod

    def fake_create_profile(name, **kwargs):
        profile_dir = Path(secretary_home) / ".plobi" / "profiles" / name
        profile_dir.mkdir(parents=True, exist_ok=True)
        (profile_dir / "plobi").mkdir(exist_ok=True)
        (profile_dir / "config.yaml").write_text("model: {}\n", encoding="utf-8")
        return profile_dir

    def fake_profile_exists(name):
        if name == "default":
            return True
        return (Path(secretary_home) / ".plobi" / "profiles" / name).is_dir()

    def fake_get_profile_dir(name):
        if name == "default":
            return Path(secretary_home) / ".plobi"
        return Path(secretary_home) / ".plobi" / "profiles" / name

    monkeypatch.setattr(profiles_mod, "create_profile", fake_create_profile)
    monkeypatch.setattr(profiles_mod, "profile_exists", fake_profile_exists)
    monkeypatch.setattr(profiles_mod, "get_profile_dir", fake_get_profile_dir)
    monkeypatch.setattr("plobi_cli.profiles.create_profile", fake_create_profile)
    monkeypatch.setattr("plobi_cli.profiles.profile_exists", fake_profile_exists)
    monkeypatch.setattr("plobi_cli.profiles.get_profile_dir", fake_get_profile_dir)
    return secretary_home


def _agenda_pipeline(tmp_path, *, fail: bool):
    service = AgendaService(tmp_path / "agenda.db")
    client = _FakeChatlog(fail=fail)
    pipe = ChatlogPipeline(
        config=CollectorConfig(talkers=["班级群"], enabled=True),
        client=client,
        service=service,
        seen=SeenStore(tmp_path / "seen.db"),
        talkers=TalkerStore(tmp_path / "talkers.db"),
        confirmer=HeuristicConfirmer(),
    )
    return pipe, service, client


def test_refresh_agenda_healthy_returns_board(tmp_path):
    pipe, service, client = _agenda_pipeline(tmp_path, fail=False)
    service.create_manual(title="组会", start_at=_tomorrow_iso(14), kind="meeting")

    report, summary = refresh_agenda(pipeline=pipe)
    assert client.health_checks >= 1
    assert report.scanned == 0  # whitelist talker has no new messages
    titles = [event["title"] for event in summary["events"]]
    assert "组会" in titles
    assert summary["tomorrow"]


def test_refresh_agenda_unhealthy_raises(tmp_path):
    pipe, _service, client = _agenda_pipeline(tmp_path, fail=True)
    with pytest.raises(ChatlogDead, match="采集不通"):
        refresh_agenda(pipeline=pipe)
    assert client.health_checks >= 1


def test_secretary_ask_unhealthy_is_error_not_fake_board(stub_spawn, tmp_path):
    """Dead door: chatlog down → ok=False, no invented events."""
    pipe, _service, _client = _agenda_pipeline(tmp_path, fail=True)
    out = run_secretary_ask(
        "refresh_agenda",
        "明天的日常安排是什么",
        pipeline=pipe,
    )
    assert out["ok"] is False
    assert "采集不通" in out["error"]
    assert out.get("dead") is True
    assert "agenda" not in out
    assert "plan" not in out


def test_secretary_ask_healthy_refresh(stub_spawn, tmp_path):
    pipe, service, _client = _agenda_pipeline(tmp_path, fail=False)
    service.create_manual(title="高数课", start_at=_tomorrow_iso(8), kind="class")

    out = run_secretary_ask(
        "refresh_agenda",
        "明天的日常安排是什么",
        pipeline=pipe,
    )
    assert out["ok"] is True
    assert out["intent"] == "refresh_agenda"
    assert out["agent"]["role"] == "l2_agenda"
    titles = [event["title"] for event in out["agenda"]["events"]]
    assert "高数课" in titles


def test_secretary_ask_spawns_one_agenda_when_missing(stub_spawn, tmp_path):
    pipe, _service, _client = _agenda_pipeline(tmp_path, fail=False)
    reg = AgentRegistry.load()
    assert find_agenda_agent(reg) is None

    out = run_secretary_ask(
        "refresh_agenda",
        "明天的日常安排是什么",
        registry=reg,
        pipeline=pipe,
    )
    assert out["ok"] is True
    assert out["agent"]["spawned"] is True
    assert out["agent"]["id"] in {"agenda", "secretary-agenda"}
    # S2: still exactly one agenda L2
    reloaded = AgentRegistry.load()
    assert len([e for e in reloaded.entries() if e.role == "l2_agenda"]) == 1


def test_secretary_ask_does_not_spawn_second_agenda(stub_spawn, tmp_path):
    pipe, _service, _client = _agenda_pipeline(tmp_path, fail=False)
    reg = AgentRegistry.load()
    first = run_secretary_ask(
        "refresh_agenda", "明天安排", registry=reg, pipeline=pipe
    )
    second = run_secretary_ask(
        "refresh_agenda", "明天安排", registry=reg, pipeline=pipe
    )
    assert first["ok"] and second["ok"]
    assert second["agent"]["spawned"] is False
    assert first["agent"]["id"] == second["agent"]["id"]


def test_secretary_ask_rejects_bad_intent(stub_spawn):
    out = run_secretary_ask("kanban_dispatch", "把任务派出去")
    assert out["ok"] is False
    assert "intent" in out["error"]


def test_handle_secretary_ask_unhealthy_json(stub_spawn, tmp_path, monkeypatch):
    """Tool JSON: unhealthy chatlog is an error envelope, not a fake board."""
    pipe, _service, _client = _agenda_pipeline(tmp_path, fail=True)
    real = run_secretary_ask

    def _with_pipe(intent, user_text, **kwargs):
        kwargs["pipeline"] = pipe
        return real(intent, user_text, **kwargs)

    monkeypatch.setattr("plobi.agents.registry.run_secretary_ask", _with_pipe)
    out = _call(
        MT.handle_secretary_ask,
        intent="refresh_agenda",
        user_text="明天的日常安排是什么",
    )
    assert out["ok"] is False
    assert "采集不通" in out.get("error", "")
    assert "agenda" not in out


def test_handle_secretary_ask_rejects_bad_intent_json():
    out = _call(MT.handle_secretary_ask, intent="kanban", user_text="明天安排")
    assert out["ok"] is False
    assert "intent" in out.get("error", "")


# ---------------------------------------------------------------------------
# WP-BE-6: N3 — user original + L1 briefing in the L2 session store
# ---------------------------------------------------------------------------


def test_n3_writes_user_text_and_briefing(stub_spawn, tmp_path):
    pipe, _service, _client = _agenda_pipeline(tmp_path, fail=False)
    user_text = "明天的日常安排是什么"
    out = run_secretary_ask("refresh_agenda", user_text, pipeline=pipe)
    assert out["ok"] is True
    sid = out["sessionId"]
    assert sid

    from plobi_cli.profiles import get_profile_dir
    from plobi_state import SessionDB

    db = SessionDB(db_path=get_profile_dir(out["agent"]["profile"]) / "state.db")
    msgs = db.get_messages(sid)
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[0]["content"] == user_text
    brief = msgs[1]["content"]
    assert "【L1 派工简报】" in brief
    assert "refresh_agenda" in brief
    assert "刷新" in brief
    # Must not dump an L1 transcript
    assert "transcript" not in brief.lower()
    assert user_text not in brief


def test_n3_not_written_when_chatlog_dead(stub_spawn, tmp_path):
    pipe, _service, _client = _agenda_pipeline(tmp_path, fail=True)
    out = run_secretary_ask(
        "refresh_agenda", "明天的日常安排是什么", pipeline=pipe
    )
    assert out["ok"] is False
    assert "sessionId" not in out


def test_n3_session_is_the_profile_latest(stub_spawn, tmp_path):
    """WP-BE-3 overview.sessionId = latest session in that profile's state.db."""
    pipe, _service, _client = _agenda_pipeline(tmp_path, fail=False)
    out = run_secretary_ask("refresh_agenda", "明天安排", pipeline=pipe)
    assert out["ok"] is True

    from plobi_cli.profiles import get_profile_dir
    from plobi.console.router import _latest_session_for_profile

    latest = _latest_session_for_profile(out["agent"]["profile"])
    assert latest == out["sessionId"]
    assert (get_profile_dir(out["agent"]["profile"]) / "state.db").is_file()


# ---------------------------------------------------------------------------
# WP-BE-7: write_briefing → aigw workbuddy / F2 fallback
# ---------------------------------------------------------------------------


def test_write_briefing_route_workbuddy(stub_spawn, tmp_path):
    pipe, service, _client = _agenda_pipeline(tmp_path, fail=False)
    service.create_manual(title="高数课", start_at=_tomorrow_iso(8), kind="class")
    out = run_secretary_ask(
        "write_briefing",
        "根据明天的日程写一段早报",
        pipeline=pipe,
        aigw_complete=lambda prompt: "明早有高数课，出门带书。",
    )
    assert out["ok"] is True
    assert out["route"] == "workbuddy"
    assert "高数" in out["briefing"] or "早" in out["briefing"]
    assert out["model"].startswith("workbuddy/") or out["model"]


def test_write_briefing_route_fallback(stub_spawn, tmp_path):
    pipe, _service, _client = _agenda_pipeline(tmp_path, fail=False)

    def boom(_prompt):
        raise RuntimeError("aigw down")

    out = run_secretary_ask(
        "write_briefing",
        "根据明天的日程写一段早报",
        pipeline=pipe,
        aigw_complete=boom,
        fallback_complete=lambda prompt: "（便宜 API）明早组会。",
    )
    assert out["ok"] is True
    assert out["route"] == "fallback"
    assert "组会" in out["briefing"] or "便宜" in out["briefing"]


def test_quota_gateway_provider_block_is_no_longer_hand_written_into_config():
    """裁定 45 第②半 + 「配置模型的入口一定要收敛成一个」。

    ``ensure_aigw_provider`` 以前往根 config.yaml 和分身 config.yaml 里手搓一份
    ``providers.aigw`` + 一条 ``custom_providers`` 条目，其 ``"name": "aigw"`` 会被
    自定义提供商行按自己的名字**原样渲染成选择器分组标题**（屏幕上出现 aigw 的活路径）。
    自 ``e9b6034`` 起 aigw 是注册过的 provider 插件
    （``plugins/model-providers/aigw``，``display_name="Local Quota Hub"``），那份手搓
    块就是第二真源，所以整套机制连同两处调用一起退役；早报直连
    :func:`aigw_base_url` / :func:`aigw_api_key`，不读那份 config。

    控制组在同一条 grep 里：`aigw_base_url` 仍在源码里（证明这条扫描看得见 aigw
    相关代码，不是恒真）。
    """
    import pathlib

    import plobi.agents.registry as registry_mod

    source = pathlib.Path(registry_mod.__file__).read_text(encoding="utf-8")
    # 只看可执行行——解释这次退役的注释本身会提到被删掉的那些字面量。
    code = "\n".join(
        line for line in source.splitlines() if not line.strip().startswith("#")
    )
    assert "ensure_aigw_provider" not in code
    assert '"name": "aigw"' not in code
    assert 'providers["aigw"]' not in code
    assert 'cfg["custom_providers"]' not in code
    # 控制组
    assert "def aigw_base_url" in code
    assert "aigw_base_url()" in code


def test_quota_gateway_error_text_names_the_gateway_not_the_internal_slug(monkeypatch):
    """错误卡会把异常文本原样上屏（裁定 45.3 露出点），所以里面不许有内部 slug。"""
    import json
    import urllib.request

    from plobi.agents.registry import _openai_chat_complete

    class _Resp:
        def __init__(self, payload):
            self._payload = payload

        def read(self):
            return json.dumps(self._payload).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def _call(request, timeout=None):
        return _Resp(_FAKE_PAYLOAD["value"])

    _FAKE_PAYLOAD = {"value": {"choices": [{"message": {"content": "明早组会。"}}]}}
    monkeypatch.setattr(urllib.request, "urlopen", _call)

    # 控制组：这条函数在正常响应下走通 —— 下面「消息里没有 aigw」不是因为根本没跑到。
    assert _openai_chat_complete(
        "http://127.0.0.1:8000/v1", "k", "workbuddy/deepseek-chat",
        [{"role": "user", "content": "hi"}],
    ) == "明早组会。"

    for payload, needle in (
        ({"choices": []}, "候选"),
        ({"choices": [{"message": {"content": "   "}}]}, "空"),
    ):
        _FAKE_PAYLOAD["value"] = payload
        with pytest.raises(RuntimeError) as excinfo:
            _openai_chat_complete(
                "http://127.0.0.1:8000/v1", "k", "workbuddy/deepseek-chat",
                [{"role": "user", "content": "hi"}],
            )
        message = str(excinfo.value)
        assert "aigw" not in message, message
        assert needle in message, message


def test_pick_workbuddy_prefers_listed_id():
    from plobi.agents.registry import pick_workbuddy_model

    assert pick_workbuddy_model(["gpt-4", "workbuddy/kimi-k3"]) == "workbuddy/kimi-k3"
    assert pick_workbuddy_model([]).startswith("workbuddy/")


# ---------------------------------------------------------------------------
# WP-C-WIRE: last-night plan on plobi_secretary_ask (no new intent)
# ---------------------------------------------------------------------------


def _tomorrow_date() -> str:
    return (datetime.now().date() + timedelta(days=1)).isoformat()


def _seed_nightly_plan(service, *, status: str, summary: str, event=None, conflict_count: int = 0):
    from plobi.agenda import store

    tomorrow = _tomorrow_date()
    conn = store.connect(service.db_path)
    try:
        plan = store.upsert_daily_plan(
            conn,
            for_date=tomorrow,
            status=status,
            summary=summary,
            event_count=1 if event is not None else 0,
            conflict_count=conflict_count,
        )
        if event is not None:
            store.replace_plan_items(
                conn,
                plan.id,
                [
                    {
                        "event_id": event.id,
                        "title": event.title,
                        "start_at": event.start_at,
                        "kind": event.kind,
                        "evidence": {"kind": "event", "event_id": event.id},
                    }
                ],
            )
        return plan
    finally:
        conn.close()


def test_secretary_ask_exposes_plan_status(stub_spawn, tmp_path):
    pipe, service, _client = _agenda_pipeline(tmp_path, fail=False)
    event = service.create_manual(title="组会", start_at=_tomorrow_iso(10), kind="meeting")
    _seed_nightly_plan(
        service,
        status="pending",
        summary="明天上午组会，冲突 0",
        event=event,
    )

    out = run_secretary_ask("refresh_agenda", "明天安排", pipeline=pipe)
    assert out["ok"] is True
    assert out["plan"]["status"] == "pending"
    assert out["plan"]["summary"] == "明天上午组会，冲突 0"
    assert out["plan"]["for_date"] == _tomorrow_date()
    assert out["plan"]["event_count"] == 1
    assert out["plan"]["conflict_count"] == 0
    assert out["plan"]["stale"] is False
    assert out["plan"]["empty"] is False


def test_secretary_ask_plan_stale_when_event_ids_differ(stub_spawn, tmp_path):
    pipe, service, _client = _agenda_pipeline(tmp_path, fail=False)
    service.create_manual(title="新课", start_at=_tomorrow_iso(8), kind="class")
    _seed_nightly_plan(
        service,
        status="pending",
        summary="昨夜按旧课表写的",
    )

    out = run_secretary_ask("refresh_agenda", "明天安排", pipeline=pipe)
    assert out["ok"] is True
    assert out["plan"]["status"] == "pending"
    assert out["plan"]["stale"] is True
    assert "以看板为准" in out["plan"]["summary"]


def test_secretary_ask_missing_plan_is_honest(stub_spawn, tmp_path):
    pipe, service, _client = _agenda_pipeline(tmp_path, fail=False)
    service.create_manual(title="组会", start_at=_tomorrow_iso(10), kind="meeting")

    out = run_secretary_ask("refresh_agenda", "明天安排", pipeline=pipe)
    assert out["ok"] is True
    assert out["plan"]["status"] == "missing"
    assert out["plan"]["summary"] == "昨夜未生成计划"
    assert "组会" not in out["plan"]["summary"]
    assert out["plan"]["empty"] is False


def test_secretary_ask_dead_does_not_fill_with_plan(stub_spawn, tmp_path):
    pipe, service, _client = _agenda_pipeline(tmp_path, fail=True)
    _seed_nightly_plan(
        service,
        status="confirmed",
        summary="昨夜写过明天组会",
    )
    out = run_secretary_ask("refresh_agenda", "明天安排", pipeline=pipe)
    assert out["ok"] is False
    assert out.get("dead") is True
    assert not out.get("plan")


def test_write_briefing_uses_plan_summary(stub_spawn, tmp_path):
    pipe, service, _client = _agenda_pipeline(tmp_path, fail=False)
    event = service.create_manual(title="高数课", start_at=_tomorrow_iso(8), kind="class")
    plan_summary = "明天早八高数，记得带书"
    _seed_nightly_plan(
        service,
        status="confirmed",
        summary=plan_summary,
        event=event,
        conflict_count=1,
    )

    captured: dict = {}

    def capture(prompt):
        captured["prompt"] = prompt
        return "按昨夜计划：早八高数。"

    out = run_secretary_ask(
        "write_briefing",
        "根据明天的日程写一段早报",
        pipeline=pipe,
        aigw_complete=capture,
    )
    assert out["ok"] is True
    assert out["route"] == "workbuddy"
    assert plan_summary in captured["prompt"]
    assert "冲突数：1" in captured["prompt"]
    assert f"{event.start_at} {event.title}" not in captured["prompt"]
    assert "- " not in captured["prompt"].split("昨夜计划：", 1)[-1]


def test_write_briefing_dismissed_uses_agenda_summary(stub_spawn, tmp_path):
    pipe, service, _client = _agenda_pipeline(tmp_path, fail=False)
    event = service.create_manual(title="高数课", start_at=_tomorrow_iso(8), kind="class")
    _seed_nightly_plan(
        service,
        status="dismissed",
        summary="昨夜计划已被忽略",
        event=event,
    )

    captured: dict = {}

    def capture(prompt):
        captured["prompt"] = prompt
        return "看板：明早高数课。"

    out = run_secretary_ask(
        "write_briefing",
        "根据明天的日程写一段早报",
        pipeline=pipe,
        aigw_complete=capture,
    )
    assert out["ok"] is True
    assert out["route"] == "workbuddy"
    assert out["plan"]["status"] == "dismissed"
    assert "高数课" in captured["prompt"]
    assert "昨夜计划已被忽略" not in captured["prompt"]
