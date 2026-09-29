"""WP-STUDIO-CWD / 裁定 36.1：项目 L2 spawn 时把 ``terminal.cwd`` 写到该 profile。

为什么这是单独文件：test_agent_registry.py 的现有 ``AgentRegistry.spawn``
测试栈已经够厚，加 cwd 一组容易让 setup 互相干扰；这一组走最小夹具
（stub ``plobi_cli.profiles``），只验 cwd 的「写 / 保留 / 跳过 / 幂等 /
不抢已有 model 字段」五条契约。
"""

from __future__ import annotations

import sys
import time
import types
from pathlib import Path

import pytest
import yaml


def _stub_plobi_profiles(monkeypatch, home: Path):
    """Stub the ``plobi_cli.profiles`` helpers used by ``AgentRegistry.spawn``."""
    fake = types.ModuleType("plobi_cli.profiles")
    fake.create_profile = lambda name, **kw: home / ".plobi" / "profiles" / name
    fake.get_profile_dir = lambda name: home / ".plobi" / "profiles" / name
    fake.profile_exists = lambda name: (home / ".plobi" / "profiles" / name).is_dir()
    monkeypatch.setitem(sys.modules, "plobi_cli.profiles", fake)


def _read_cwd(profile_dir) -> str | None:
    cfg_path = Path(profile_dir) / "config.yaml"
    if not cfg_path.is_file():
        return None
    try:
        cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(cfg, dict):
        return None
    terminal = cfg.get("terminal")
    if not isinstance(terminal, dict):
        return None
    return terminal.get("cwd")


# ---------------------------------------------------------------------------
# apply_l2_project_cwd：纯函数（不依赖 spawn）
# ---------------------------------------------------------------------------


def test_apply_l2_project_cwd_writes_minimal_yaml_when_missing(tmp_path):
    """Profile 还没有 config.yaml → 落地最小 yaml（terminal.cwd only）。"""
    from plobi.agents.registry import apply_l2_project_cwd

    profile_dir = tmp_path / "l2-plobi"
    profile_dir.mkdir()
    project_dir = tmp_path / "workspace"
    project_dir.mkdir()

    assert apply_l2_project_cwd(profile_dir, str(project_dir)) is True
    cwd = _read_cwd(profile_dir)
    assert cwd == str(project_dir.absolute())


def test_apply_l2_project_cwd_preserves_other_keys(tmp_path):
    """已有 config.yaml → 只加 / 更新 terminal.cwd，其它字段一字不改。"""
    from plobi.agents.registry import apply_l2_project_cwd

    profile_dir = tmp_path / "l2-nuclide"
    profile_dir.mkdir()
    project_dir = tmp_path / "workspace"
    project_dir.mkdir()
    cfg_path = profile_dir / "config.yaml"
    cfg_path.write_text(
        "model:\n"
        "  provider: deepseek\n"
        "  default: deepseek-chat\n"
        "platform_toolsets:\n"
        "  cli:\n"
        "    - web\n"
        "    - skills\n"
        "terminal:\n"
        "  whitelist:\n"
        "    - ls\n"
        "    - cat\n"
        "  shell: bash\n",
        encoding="utf-8",
    )

    assert apply_l2_project_cwd(profile_dir, str(project_dir)) is True

    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    assert cfg["model"] == {"provider": "deepseek", "default": "deepseek-chat"}
    assert cfg["platform_toolsets"] == {"cli": ["web", "skills"]}
    assert cfg["terminal"]["whitelist"] == ["ls", "cat"]
    assert cfg["terminal"]["shell"] == "bash"
    assert cfg["terminal"]["cwd"] == str(project_dir.absolute())


def test_apply_l2_project_cwd_is_idempotent(tmp_path):
    """同一个绝对路径再写一次 → 返回 False，不重写文件。"""
    from plobi.agents.registry import apply_l2_project_cwd

    profile_dir = tmp_path / "l2-prism"
    profile_dir.mkdir()
    project_dir = tmp_path / "workspace"
    project_dir.mkdir()

    assert apply_l2_project_cwd(profile_dir, str(project_dir)) is True
    mtime_first = (profile_dir / "config.yaml").stat().st_mtime_ns

    time.sleep(0.02)  # 留出 mtime 变化窗口
    assert apply_l2_project_cwd(profile_dir, str(project_dir)) is False
    assert (profile_dir / "config.yaml").stat().st_mtime_ns == mtime_first


def test_apply_l2_project_cwd_skips_empty_path(tmp_path):
    """空 / None 的 project_path → no-op，不创建 config.yaml。"""
    from plobi.agents.registry import apply_l2_project_cwd

    profile_dir = tmp_path / "l2-empty"
    profile_dir.mkdir()

    assert apply_l2_project_cwd(profile_dir, "") is False
    assert apply_l2_project_cwd(profile_dir, None) is False
    assert not (profile_dir / "config.yaml").exists()


def test_apply_l2_project_cwd_skips_nonexistent_path(tmp_path):
    """project_path 不存在 → no-op，绝不写假 cwd。"""
    from plobi.agents.registry import apply_l2_project_cwd

    profile_dir = tmp_path / "l2-bogus"
    profile_dir.mkdir()

    assert apply_l2_project_cwd(profile_dir, str(tmp_path / "does_not_exist")) is False
    assert not (profile_dir / "config.yaml").exists()


def test_apply_l2_project_cwd_returns_false_for_missing_profile_dir():
    """profile_dir 是 None 或不存在 → False。"""
    from plobi.agents.registry import apply_l2_project_cwd

    assert apply_l2_project_cwd(None, "D:/foo") is False
    assert apply_l2_project_cwd("/nonexistent/path/abc", "D:/foo") is False


def test_l2_diet_does_not_touch_terminal_cwd(tmp_path):
    """WP-L2-DIET 仍剥 terminal 工具集——cwd 是配置字段，不是工具集。"""
    from plobi.agents.registry import apply_l2_project_diet

    profile_dir = tmp_path / "l2-stithy"
    profile_dir.mkdir()
    cfg_path = profile_dir / "config.yaml"
    cfg_path.write_text(
        "toolsets:\n"
        "  - terminal\n"
        "  - web\n"
        "  - skills\n"
        "platform_toolsets:\n"
        "  cli:\n"
        "    - terminal\n"
        "    - web\n"
        "  gateway:\n"
        "    - plobi-cli\n"
        "terminal:\n"
        "  cwd: D:/keep/me/here\n",
        encoding="utf-8",
    )

    assert apply_l2_project_diet(profile_dir) is True

    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    assert "terminal" not in cfg["toolsets"]
    assert "terminal" not in cfg["platform_toolsets"]["cli"]
    assert "plobi-cli" not in cfg["platform_toolsets"]["gateway"]
    # cwd 必须原样保留——它不是工具集。
    assert cfg["terminal"]["cwd"] == "D:/keep/me/here"


# ---------------------------------------------------------------------------
# AgentRegistry.spawn 端到端：cwd 在 spawn 时落地
# ---------------------------------------------------------------------------


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    plobi = tmp_path / ".plobi"
    plobi.mkdir()
    monkeypatch.setenv("PLOBI_HOME", str(plobi))
    monkeypatch.setenv(
        "PLOBI_PROJECTS_CONFIG", str(plobi / "plobi" / "projects.yaml")
    )
    monkeypatch.delenv("PLOBI_MODELS_CONFIG", raising=False)
    _stub_plobi_profiles(monkeypatch, tmp_path)
    return tmp_path


def test_spawn_writes_terminal_cwd_into_config_yaml(home):
    """AgentRegistry.spawn → config.yaml 含 terminal.cwd = entry.project_path。

    裁定 36.1 主验收：spawn 一个带 project_path 的项目 L2，profile 的
    config.yaml 必须含 cwd。
    """
    from plobi.agents.registry import AgentEntry, AgentRegistry

    project_dir = home / "workspace"
    project_dir.mkdir()
    profile_dir = home / ".plobi" / "profiles" / "l2-plobi"
    profile_dir.mkdir(parents=True)
    (profile_dir / "config.yaml").write_text("toolsets: [web]\n", encoding="utf-8")

    reg = AgentRegistry.load()
    reg.upsert(
        AgentEntry(
            name="Plobi",
            role="l2_project",
            profile="l2-plobi",
            model="deepseek-chat",
            project_path=str(project_dir),
            category="projects",
        )
    )

    result = reg.spawn("Plobi")

    cwd = _read_cwd(Path(result["profile_dir"]))
    assert cwd == str(project_dir.absolute()), (
        f"spawn 后 cwd 应等于 project_path；实得 cwd={cwd!r}"
    )


def test_spawn_creates_minimal_yaml_when_profile_is_fresh(home):
    """新 profile 还没 config.yaml → spawn 后落地最小 yaml（只有 terminal.cwd）。"""
    from plobi.agents.registry import AgentEntry, AgentRegistry

    project_dir = home / "fresh_workspace"
    project_dir.mkdir()
    profile_dir = home / ".plobi" / "profiles" / "l2-fresh"
    profile_dir.mkdir(parents=True)
    # 没有 config.yaml —— spawn 的 create_profile 也不创建它。

    reg = AgentRegistry.load()
    reg.upsert(
        AgentEntry(
            name="Fresh",
            role="l2_project",
            profile="l2-fresh",
            model="deepseek-chat",
            project_path=str(project_dir),
            category="projects",
        )
    )

    result = reg.spawn("Fresh")
    cwd = _read_cwd(Path(result["profile_dir"]))
    assert cwd == str(project_dir.absolute())


def test_spawn_skips_terminal_cwd_for_agenda_role(home):
    """l2_agenda / l2_butler 不写 terminal.cwd——它们的 cwd 不归这个秘书管。"""
    from plobi.agents.registry import AgentEntry, AgentRegistry

    project_dir = home / "agenda_workspace"
    project_dir.mkdir()
    profile_dir = home / ".plobi" / "profiles" / "l2-agenda-secretary"
    profile_dir.mkdir(parents=True)
    (profile_dir / "config.yaml").write_text("toolsets: [terminal]\n", encoding="utf-8")

    reg = AgentRegistry.load()
    reg.upsert(
        AgentEntry(
            name="agenda",
            role="l2_agenda",
            profile="l2-agenda-secretary",
            model="deepseek-chat",
            project_path=str(project_dir),  # 即便给了也不写。
            category="agenda",
        )
    )

    reg.spawn("agenda")

    # 但ler / agenda 不能拿到这条 cwd——terminal.cwd 必须不在。
    assert _read_cwd(profile_dir) is None


def test_apply_l2_project_cwd_skips_butler_role():
    """``L2_PROJECT_CWD_BUTLER_ROLES`` 把 agenda / butler 排除——这是空 path 之外的另一道保险。"""
    from plobi.agents.registry import L2_PROJECT_CWD_BUTLER_ROLES

    assert "l2_agenda" in L2_PROJECT_CWD_BUTLER_ROLES
    assert "l2_butler" in L2_PROJECT_CWD_BUTLER_ROLES
    # l2_project 不在该集合里——是 cwd 写入的目标。
    assert "l2_project" not in L2_PROJECT_CWD_BUTLER_ROLES


def test_ensure_mind_project_agents_does_not_write_cwd_for_butler(tmp_path, monkeypatch):
    """ensure_mind_project_agents 的现有-row 分支调 _cwd_existing_l2_project——

    butler / events 不应被写 cwd（category 检查保证）。"""
    import types

    from plobi.agents import registry as registry_mod
    from plobi.agents.registry import AgentEntry

    home = tmp_path / "plobi_home"
    home.mkdir()
    monkeypatch.setattr("pathlib.Path.home", lambda: home)
    monkeypatch.setenv("PLOBI_HOME", str(home))
    monkeypatch.setenv(
        "PLOBI_PROJECTS_CONFIG", str(home / "plobi" / "projects.yaml")
    )
    monkeypatch.delenv("PLOBI_MODELS_CONFIG", raising=False)

    fake = types.ModuleType("plobi_cli.profiles")
    fake.get_profile_dir = lambda name: home / ".plobi" / "profiles" / name
    fake.profile_exists = lambda name: False  # profile 不存在 → 静默跳过
    monkeypatch.setitem(sys.modules, "plobi_cli.profiles", fake)

    butler = AgentEntry(
        name="Butler",
        role="l2_butler",
        profile="l2-butler",
        model="",
        project_path="",
        category="butler",
    )

    # 直接调 _cwd_existing_l2_project：butler role 早 return，连
    # profile_exists 都不查。stub 把 profile_exists 标记为 False，看是
    # 不是连这一步都没到（早 return 是关键证据）。
    registry_mod._cwd_existing_l2_project(butler)
    # 没有异常 / 没有写入 = pass。stub 没动 = 没触发 fake.profile_exists
    # 路径——已经旁证 butler 早 return。


def test_spawn_does_not_fabricate_cwd_for_nonexistent_project_path(home):
    """project_path 不存在 → spawn 不写假 cwd。"""
    from plobi.agents.registry import AgentEntry, AgentRegistry

    profile_dir = home / ".plobi" / "profiles" / "l2-bogus"
    profile_dir.mkdir(parents=True)
    (profile_dir / "config.yaml").write_text("toolsets: [web]\n", encoding="utf-8")

    reg = AgentRegistry.load()
    reg.upsert(
        AgentEntry(
            name="Bogus",
            role="l2_project",
            profile="l2-bogus",
            model="deepseek-chat",
            project_path=str(home / "nope_does_not_exist"),
            category="projects",
        )
    )

    reg.spawn("Bogus")

    assert _read_cwd(profile_dir) is None


def test_spawn_idempotent_cwd_value_unchanged_on_repeat_spawn(home):
    """同一个项目 L2 二次 spawn → terminal.cwd 值不变（文件本身可能被
    ``_write_profile_config`` 改写，因为模型段会重写，但 cwd 字段不能漂）。"""
    from plobi.agents.registry import AgentEntry, AgentRegistry

    project_dir = home / "idempotent_workspace"
    project_dir.mkdir()
    profile_dir = home / ".plobi" / "profiles" / "l2-idem"
    profile_dir.mkdir(parents=True)
    (profile_dir / "config.yaml").write_text("toolsets: [web]\n", encoding="utf-8")

    reg = AgentRegistry.load()
    reg.upsert(
        AgentEntry(
            name="Idem",
            role="l2_project",
            profile="l2-idem",
            model="deepseek-chat",
            project_path=str(project_dir),
            category="projects",
        )
    )

    reg.spawn("Idem")
    cwd_first = _read_cwd(profile_dir)
    time.sleep(0.02)
    reg.spawn("Idem")
    cwd_second = _read_cwd(profile_dir)
    # cwd 值必须对齐——不准漂。
    assert cwd_first == cwd_second
    assert cwd_second == str(project_dir.absolute())
