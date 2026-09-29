"""B2: L2 常驻 Agent 注册表 — registry round-trip / routing 断言 / spawn。

注册表 = $PLOBI_HOME/plobi/projects.yaml（profile-safe）；spawn 落地 profile
（复用 plobi_cli.profiles.create_profile）+ 写 profile 级 plobi/models.json
（ADR-0011 断言绿）+ 同步 config.yaml 模型。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from plobi.agents.registry import AgentEntry, AgentRegistry, RegistryError, default_path


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """隔离 PLOBI_HOME + Path.home()（run_tests.sh 的 env -i 丢 USERPROFILE）。"""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("PLOBI_HOME", str(tmp_path / ".plobi"))
    monkeypatch.delenv("PLOBI_MODELS_CONFIG", raising=False)
    monkeypatch.delenv("PLOBI_PROJECTS_CONFIG", raising=False)
    return tmp_path


def _entry(**kw):
    defaults = dict(name="agenda", role="l2_agenda", model="deepseek-chat")
    defaults.update(kw)
    return AgentEntry(**defaults)


# ---------------------------------------------------------------------------
# default_path & IO
# ---------------------------------------------------------------------------


def test_default_path_is_plobi_home_anchored(home):
    assert default_path() == Path(home) / ".plobi" / "plobi" / "projects.yaml"


def test_load_missing_returns_empty(home):
    reg = AgentRegistry.load()
    assert reg.agents == {}
    assert reg.path == default_path()


def test_round_trip_preserves_entries(home):
    reg = AgentRegistry.load()
    reg.upsert(_entry(name="agenda", skills=("a", "b"), description="日程闭环"))
    reg.upsert(_entry(name="srtp", role="l2_project", profile="l2-srtp"))
    path = reg.save()

    assert path.is_file()
    reloaded = AgentRegistry.load(path)
    assert set(reloaded.names()) == {"agenda", "srtp"}
    assert reloaded.get("agenda").skills == ("a", "b")
    assert reloaded.get("agenda").description == "日程闭环"
    assert reloaded.get("srtp").profile_name == "l2-srtp"


def test_missing_profile_field_falls_back_to_name(home):
    reg = AgentRegistry.load()
    reg.upsert(_entry(name="x", profile=""))
    assert reg.get("x").profile_name == "x"


# ---------------------------------------------------------------------------
# routing 断言（ADR-0011：L1≠L2，L1 不得 GUI-only）
# ---------------------------------------------------------------------------


def test_registry_with_l1_and_l2_is_green(home):
    reg = AgentRegistry.load()
    reg.upsert(_entry(name="secretary", role="l1_secretary", provider="moonshot", model="kimi-k3"))
    reg.upsert(_entry(name="agenda", role="l2_agenda", model="deepseek-chat"))
    assert reg.routing_problems() == []


def test_l2_sharing_l1_model_is_flagged(home):
    reg = AgentRegistry.load()
    reg.upsert(_entry(name="secretary", role="l1_secretary", provider="moonshot", model="kimi-k3"))
    reg.upsert(_entry(name="bad", role="l2_agenda", provider="moonshot", model="kimi-k3"))
    problems = reg.routing_problems()
    assert any("shares L1" in p for p in problems)


def test_upsert_removes_and_round_trips(home):
    reg = AgentRegistry.load()
    reg.upsert(_entry(name="agenda"))
    assert reg.remove("agenda") is True
    assert reg.remove("nope") is False


# ---------------------------------------------------------------------------
# spawn
# ---------------------------------------------------------------------------


@pytest.fixture()
def spawn_env(home, monkeypatch):
    """把 spawn 的 create_profile 缩到最小：只 mkdir，不碰真实 profile 逻辑。

    spawn 在测试里必须验证 profile 目录 + models.json + config.yaml 三个产物。
    create_profile 会走 seed skills / clone 等重逻辑，直接替换成 mkdir 即可。
    """
    import plobi_cli.profiles as profiles_mod

    def fake_create_profile(name, **kwargs):
        profile_dir = Path(home) / ".plobi" / "profiles" / name
        profile_dir.mkdir(parents=True, exist_ok=True)
        (profile_dir / "plobi").mkdir(exist_ok=True)
        (profile_dir / "config.yaml").write_text("model: {}\n", encoding="utf-8")
        return profile_dir

    def fake_profile_exists(name):
        return (Path(home) / ".plobi" / "profiles" / name).is_dir()

    monkeypatch.setattr(profiles_mod, "create_profile", fake_create_profile)
    monkeypatch.setattr(profiles_mod, "profile_exists", fake_profile_exists)
    monkeypatch.setattr("plobi_cli.profiles.create_profile", fake_create_profile)
    monkeypatch.setattr("plobi_cli.profiles.profile_exists", fake_profile_exists)
    return home


def test_spawn_creates_profile_and_models(spawn_env):
    reg = AgentRegistry.load()
    reg.upsert(_entry(name="secretary", role="l1_secretary", provider="moonshot", model="kimi-k3"))
    reg.upsert(_entry(name="agenda", role="l2_agenda", provider="deepseek", model="deepseek-chat"))
    reg.save()

    result = reg.spawn("agenda")

    assert result["routing_ok"] is True
    profile_dir = Path(result["profile_dir"])
    assert profile_dir.is_dir()
    models = json.loads((profile_dir / "plobi" / "models.json").read_text(encoding="utf-8"))
    assert models["roles"]["l2_agenda"]["provider"] == "deepseek"
    assert models["roles"]["l2_agenda"]["model"] == "deepseek-chat"
    # ADR-0011：写进 profile 的路由表断言绿（L1≠L2）
    from plobi.routing import ModelRouter

    assert ModelRouter.load(profile_dir / "plobi" / "models.json").violations() == []
    # config.yaml 同步了模型
    cfg = (profile_dir / "config.yaml").read_text(encoding="utf-8")
    assert "deepseek-chat" in cfg


def test_spawn_routing_violation_aborts(spawn_env):
    reg = AgentRegistry.load()
    # L1 缺失 → spawn 时 profile 路由表断言失败
    reg.upsert(_entry(name="agenda", role="l2_agenda", provider="moonshot", model="kimi-k3"))
    reg.save()

    with pytest.raises(RegistryError, match="routing"):
        reg.spawn("agenda")


def test_spawn_unknown_agent_raises(home):
    reg = AgentRegistry.load()
    with pytest.raises(RegistryError, match="not registered"):
        reg.spawn("nope")


def test_spawn_reuses_existing_profile(spawn_env):
    reg = AgentRegistry.load()
    reg.upsert(_entry(name="secretary", role="l1_secretary", provider="moonshot", model="kimi-k3"))
    reg.upsert(_entry(name="agenda", role="l2_agenda", provider="deepseek", model="deepseek-chat"))
    reg.save()

    reg.spawn("agenda")
    # 第二次 spawn：profile 已存在，仍成功且复用同一目录
    result2 = reg.spawn("agenda")
    assert result2["routing_ok"] is True


# ---------------------------------------------------------------------------
# CLI（薄壳）冒烟
# ---------------------------------------------------------------------------


class _Args:
    def __init__(self, **kw):
        self.__dict__.update(kw)
        for attr in ("role", "profile", "provider", "model", "mind_subtree", "skills", "description", "clone_from", "registry"):
            if attr not in self.__dict__:
                self.__dict__[attr] = None


def test_cli_list_empty(home, capsys):
    from plobi.agents.cli import run_plobi

    run_plobi(_Args(plobi_action="agents", agents_action="list"))
    out = capsys.readouterr().out
    assert "No resident agents registered" in out


def test_cli_register_then_spawn(spawn_env, capsys, monkeypatch):
    from plobi.agents import cli

    run_plobi = cli.run_plobi

    run_plobi(_Args(
        plobi_action="agents", agents_action="register", name="secretary",
        role="l1_secretary", profile="master", provider="moonshot", model="kimi-k3",
    ))
    run_plobi(_Args(
        plobi_action="agents", agents_action="register", name="agenda",
        role="l2_agenda", provider="deepseek", model="deepseek-chat",
    ))

    run_plobi(_Args(plobi_action="agents", agents_action="spawn", name="agenda"))
    out = capsys.readouterr().out
    assert "Spawned agenda" in out
    assert "plobi -p agenda chat" in out
    # 落盘后注册表可见
    reg = AgentRegistry.load()
    assert set(reg.names()) == {"agenda", "secretary"}


def test_cli_spawn_inline_register(spawn_env, capsys):
    """spawn 未注册的 agent 且带 --role/--model → 自动注册再 spawn。"""
    from plobi.agents.cli import run_plobi

    run_plobi(_Args(
        plobi_action="agents", agents_action="spawn", name="secretary",
        role="l1_secretary", profile="master", provider="moonshot", model="kimi-k3",
    ))
    run_plobi(_Args(
        plobi_action="agents", agents_action="spawn", name="agenda",
        role="l2_agenda", provider="deepseek", model="deepseek-chat",
    ))
    out = capsys.readouterr().out
    assert "Spawned agenda" in out
    assert "ADR-0011 green" in out


# --------------------------------------------------------------------------- #
# C5 pace field (weekly_hours) on l2_project entries
# --------------------------------------------------------------------------- #


def test_pace_roundtrip_and_weekly_hours():
    entry = AgentEntry.from_dict("sim", {"role": "l2_project", "pace": {"weekly_hours": 6}})
    assert entry.weekly_hours == 6.0
    data = entry.to_dict()
    assert data["pace"] == {"weekly_hours": 6.0}
    clone = AgentEntry.from_dict("sim", data)
    assert clone.weekly_hours == 6.0


def test_pace_defaults_to_none():
    entry = AgentEntry.from_dict("sim", {"role": "l2_project"})
    assert entry.pace is None
    assert entry.weekly_hours is None
    assert "pace" not in entry.to_dict()


def test_pace_rejects_bad_values():
    with pytest.raises(RegistryError):
        AgentEntry.from_dict("sim", {"role": "l2_project", "pace": {"weekly_hours": 0}})
    with pytest.raises(RegistryError):
        AgentEntry.from_dict("sim", {"role": "l2_project", "pace": {"weekly_hours": -3}})
    with pytest.raises(RegistryError):
        AgentEntry.from_dict("sim", {"role": "l2_project", "pace": {"weekly_hours": 200}})
    with pytest.raises(RegistryError):
        AgentEntry.from_dict("sim", {"role": "l2_project", "pace": {"weekly_hours": "abc"}})


def test_registry_roundtrip_preserves_pace(home):
    registry = AgentRegistry(path=home / "projects.yaml")
    registry.upsert(AgentEntry.from_dict("sim", {"role": "l2_project", "pace": {"weekly_hours": 6}}))
    registry.save()
    reloaded = AgentRegistry.load(home / "projects.yaml")
    assert reloaded.get("sim").weekly_hours == 6.0


# ---------------------------------------------------------------------------
# WP-AGENT-DISPLAY-NAME / 裁定 37.1：display_name 字段往返 + 回退 + slug 不漂
# ---------------------------------------------------------------------------


def test_display_name_round_trips_through_yaml(home):
    reg = AgentRegistry.load()
    reg.upsert(
        _entry(name="aura", display_name="Aura", description="iPhone 项目节奏")
    )
    reg.save()

    reloaded = AgentRegistry.load(reg.path)
    entry = reloaded.get("aura")
    assert entry is not None
    assert entry.display_name == "Aura"
    # description 继续存长标语——不再承担显示名职责。
    assert entry.description == "iPhone 项目节奏"


def test_display_name_empty_falls_back_to_id(home):
    """老条目无 display_name 字段 → 回退到 entry.name（小写 slug）。"""
    raw = {
        "version": 1,
        "agents": {
            "old-agent": {"role": "l2_project", "description": "pre-AGENT-DISPLAY-NAME"},
        },
    }
    import yaml

    path = home / "plobi" / "projects.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")

    reloaded = AgentRegistry.load(path)
    entry = reloaded.get("old-agent")
    assert entry is not None
    assert entry.display_name == ""


def test_display_name_preserves_case_and_caps(home):
    """不做 title-case、不截断——「iPhone 项目」就存这个。"""
    reg = AgentRegistry.load()
    reg.upsert(_entry(name="aura", display_name="Aura"))
    reg.upsert(_entry(name="iphone-proj", display_name="iPhone 项目"))
    reg.save()

    reloaded = AgentRegistry.load(reg.path)
    assert reloaded.get("aura").display_name == "Aura"
    assert reloaded.get("iphone-proj").display_name == "iPhone 项目"
    # id 永远是小写 slug。
    assert reloaded.get("iphone-proj").name == "iphone-proj"


def test_to_dict_omits_empty_display_name(home):
    """空 display_name 不进 yaml（避免老条目 yaml 噪声增长）。"""
    reg = AgentRegistry.load()
    reg.upsert(_entry(name="naked", display_name=""))
    data = reg.get("naked").to_dict()
    assert "display_name" not in data
    reg.upsert(_entry(name="dressed", display_name="有名字"))
    data2 = reg.get("dressed").to_dict()
    assert data2["display_name"] == "有名字"


def test_from_dict_handles_missing_display_name_key(home):
    """老 yaml 没 display_name 字段 → 视为空字符串，不抛。"""
    raw = {
        "version": 1,
        "agents": {
            "legacy": {"role": "l2_project", "description": "x"},
        },
    }
    import yaml

    path = home / "plobi" / "projects.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    reloaded = AgentRegistry.load(path)
    assert reloaded.get("legacy").display_name == ""


def test_studio_cwd_still_lands_when_agent_has_display_name(home, monkeypatch):
    """WP-STUDIO-CWD / 裁定 36.1 仍跑得动——display_name 不影响 cwd 写入。

    这条是裁定 37.1 / 39 显式要求的回归：确保本刀没碰 cwd 逻辑。
    走 stub 后的 AgentRegistry.spawn（不走 _stub_spawn）以触发真实的
    _write_profile_config + apply_l2_project_cwd 路径。
    """
    from plobi.agents import registry as registry_mod
    from plobi.agents.registry import AgentEntry, AgentRegistry
    import sys, types, yaml

    # Stub plobi_cli.profiles 让 reg.spawn 能找到 profile_dir。
    fake = types.ModuleType("plobi_cli.profiles")
    fake.create_profile = lambda name, **kw: home / ".plobi" / "profiles" / name
    fake.get_profile_dir = lambda name: home / ".plobi" / "profiles" / name
    fake.profile_exists = lambda name: (home / ".plobi" / "profiles" / name).is_dir()
    monkeypatch.setitem(sys.modules, "plobi_cli.profiles", fake)
    monkeypatch.setenv("PLOBI_HOME", str(home / ".plobi"))
    monkeypatch.setenv(
        "PLOBI_PROJECTS_CONFIG", str(home / ".plobi" / "plobi" / "projects.yaml")
    )

    project_dir = home / "workspace"
    project_dir.mkdir()
    profile_dir = home / ".plobi" / "profiles" / "l2-plobi"
    profile_dir.mkdir(parents=True)
    (profile_dir / "config.yaml").write_text("toolsets: [web]\n", encoding="utf-8")

    def fake_write_profile_models(self, profile_dir, entry):
        # 跳过 routing 配置——本测试只关心 cwd 是否落地。
        from pathlib import Path

        target = Path(profile_dir) / "plobi" / "models.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("{}", encoding="utf-8")
        return target

    monkeypatch.setattr(
        AgentRegistry, "_write_profile_models", fake_write_profile_models
    )

    reg = AgentRegistry.load()
    reg.upsert(
        AgentEntry(
            name="Plobi",
            role="l2_project",
            profile="l2-plobi",
            model="deepseek-chat",
            project_path=str(project_dir),
            category="projects",
            display_name="Plobi",  # 本刀新增字段；cwd 仍要写。
        )
    )
    reg.spawn("Plobi")

    cfg = yaml.safe_load((profile_dir / "config.yaml").read_text(encoding="utf-8"))
    assert isinstance(cfg, dict)
    assert cfg.get("terminal", {}).get("cwd") == str(project_dir.absolute())


# ---------------------------------------------------------------------------
# 可解析闸门：底座认不出来的 provider **不写进 model 节**
# （症状：桌面点进分身 → 「Unknown provider 'aigw'. Check 'plobi model' …」）
# ---------------------------------------------------------------------------

_UNRESOLVABLE_PROVIDER = "no-such-provider-gate-check"


def _resolvable_provider_name() -> str:
    """从底座那个权威自己的注册表里取一个它认的名字（不写死 provider 名单）。"""
    from plobi_cli.auth import PROVIDER_REGISTRY, resolve_provider

    assert PROVIDER_REGISTRY, "底座注册表为空 —— 闸门无从验证"
    name = sorted(PROVIDER_REGISTRY)[0]
    assert resolve_provider(name) == name
    return name


def _seed_profile_dir(home, profile: str, config_text: str) -> Path:
    """预建 profile 目录 + 一份 config.yaml（spawn 见目录已存在就直接用）。"""
    profile_dir = Path(home) / ".plobi" / "profiles" / profile
    profile_dir.mkdir(parents=True, exist_ok=True)
    (profile_dir / "plobi").mkdir(exist_ok=True)
    (profile_dir / "config.yaml").write_text(config_text, encoding="utf-8")
    return profile_dir


def test_spawn_writes_model_section_when_provider_resolves(spawn_env, home):
    """① provider 可解析 → 照原样写进 config.yaml 的 model 节（原行为不变）。"""
    import yaml

    provider = _resolvable_provider_name()
    reg = AgentRegistry.load()
    reg.upsert(
        _entry(name="secretary", role="l1_secretary", provider="moonshot", model="kimi-k3")
    )
    reg.upsert(
        _entry(name="proj", role="l2_project", profile="l2-proj", provider=provider, model="a-model")
    )
    reg.save()

    result = reg.spawn("proj")

    cfg = yaml.safe_load(
        (Path(result["profile_dir"]) / "config.yaml").read_text(encoding="utf-8")
    )
    assert cfg["model"]["provider"] == provider
    assert cfg["model"]["default"] == "a-model"


def test_spawn_skips_unresolvable_provider_without_failing(spawn_env, caplog):
    """② provider 不可解析 → 不写 model 节、spawn 照常返回、留一条点名 warning。"""
    import logging
    import yaml
    from plobi_cli.auth import AuthError, resolve_provider

    # 前提自证（不是写死的名字表）：底座权威确实拒这个名字，红条就是它抛的。
    with pytest.raises(AuthError) as excinfo:
        resolve_provider(_UNRESOLVABLE_PROVIDER)
    assert excinfo.value.code == "invalid_provider"

    profile_dir = _seed_profile_dir(spawn_env, "l2-proj", "toolsets: [web]\n")
    reg = AgentRegistry.load()
    reg.upsert(
        _entry(name="secretary", role="l1_secretary", provider="moonshot", model="kimi-k3")
    )
    reg.upsert(
        _entry(
            name="proj",
            role="l2_project",
            profile="l2-proj",
            provider=_UNRESOLVABLE_PROVIDER,
            model="a-model",
        )
    )
    reg.save()

    with caplog.at_level(logging.WARNING, logger="plobi.agents.registry"):
        result = reg.spawn("proj")  # 不许抛

    assert result["routing_ok"] is True
    assert Path(result["profile_dir"]) == profile_dir
    cfg = yaml.safe_load((profile_dir / "config.yaml").read_text(encoding="utf-8"))
    assert "model" not in cfg, "不可解析的 provider 被写进了 model 节"
    assert cfg["toolsets"] == ["web"], "跳过 model 节不许动 config 的其他键"
    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any(_UNRESOLVABLE_PROVIDER in m for m in warnings), warnings
    assert any("plobi model" in m for m in warnings), warnings


def test_unresolvable_provider_skip_leaves_terminal_cwd_and_inherited_model(spawn_env, home):
    """③ 跳过 model 节不影响 terminal.cwd；分身留着它继承来的 model 配置。

    cwd 与模型是两件事（WP-STUDIO-CWD / 裁定 36.1 已拆开）：模型不可解析不许把
    工作目录一起带走；而"不写"的语义正是**继承**——克隆来的 model 节原样保留。
    """
    import yaml

    inherited_provider = _resolvable_provider_name()
    project_dir = Path(home) / "workspace"
    project_dir.mkdir(parents=True, exist_ok=True)
    profile_dir = _seed_profile_dir(
        spawn_env,
        "l2-proj",
        yaml.safe_dump(
            {"model": {"provider": inherited_provider, "default": "inherited-model"}},
            allow_unicode=True,
        ),
    )

    reg = AgentRegistry.load()
    reg.upsert(
        _entry(name="secretary", role="l1_secretary", provider="moonshot", model="kimi-k3")
    )
    reg.upsert(
        _entry(
            name="proj",
            role="l2_project",
            profile="l2-proj",
            provider=_UNRESOLVABLE_PROVIDER,
            model="a-model",
            project_path=str(project_dir),
            category="projects",
        )
    )
    reg.save()

    result = reg.spawn("proj")

    cfg = yaml.safe_load((profile_dir / "config.yaml").read_text(encoding="utf-8"))
    assert cfg["terminal"]["cwd"] == str(project_dir.absolute())
    assert cfg["model"] == {"provider": inherited_provider, "default": "inherited-model"}
    assert result["routing_ok"] is True


def test_profile_name_is_normalized_to_the_on_disk_id():
    """注册表键可以是首字母大写的显示名，但发出去的 profile 名必须是磁盘上那个小写 id。

    否则桌面 ``plobi --profile Aura serve`` 会被 CLI 的 ``-p`` 预扫描（main.py 的
    ``_PROFILE_ID_RE``）判为非法而**不摘 token**，argparse 接着把 ``Aura`` 当成子命令
    → 退出码 2 → 分身后端永远起不来、界面一直停在「网关 检查中」。
    与底座那个函数的等价性钉在 tests/plobi_cli/test_profiles.py（本层不 import plobi_cli）。
    """
    assert AgentEntry(name="Aura", role="l2_project", profile="L2-Aura").profile_name == "l2-aura"


# ---------------------------------------------------------------------------
# 裁定 45 / 42 §42.2 ⑥：没有模型覆盖的分身继承用户配置的默认，不落硬编码路由
# ---------------------------------------------------------------------------

# 用户 2026-09-29 在本机设置的默认（根 config.yaml 与 13 个分身的实测值）。
_CONFIGURED_DEFAULT = {
    "provider": "minimax-cn",
    "default": "MiniMax-M3",
    "base_url": "https://api.minimaxi.com/anthropic",
}


def test_spawn_without_model_override_inherits_the_configured_default(spawn_env, home):
    """注册表那条记录没写 provider/model 时，分身 config.yaml 的 model 节一字不改。

    反例（本次修掉的病症）：新落的 Framelet / Plobi 两条被盖成
    ``provider: aigw`` + ``default: workbuddy/deepseek-chat``，而 ``base_url`` 还是
    克隆来的 minimax —— 一个自相矛盾的三元组，用户从没选过它。它来自
    :data:`plobi.routing.models.DEFAULT_ROUTES` 的硬编码兜底：省略覆盖被当成了
    「用默认路由」，而那个默认路由和「用户当前配置的默认模型」不是一回事
    （``e9b6034`` 把 aigw 注册成真 provider 后，可解析闸门也不再拦它）。
    """
    import yaml

    profile_dir = _seed_profile_dir(
        spawn_env,
        "l2-proj",
        yaml.safe_dump({"model": dict(_CONFIGURED_DEFAULT)}, allow_unicode=True),
    )
    reg = AgentRegistry.load()
    reg.upsert(_entry(name="secretary", role="l1_secretary", provider="moonshot", model="kimi-k3"))
    reg.upsert(
        _entry(name="proj", role="l2_project", profile="l2-proj", provider="", model="")
    )
    reg.save()

    result = reg.spawn("proj")

    text = (profile_dir / "config.yaml").read_text(encoding="utf-8")
    cfg = yaml.safe_load(text)
    assert cfg["model"] == _CONFIGURED_DEFAULT, "分身被盖上了它没被选择的模型"
    assert "aigw" not in text, text
    assert "workbuddy" not in text, text
    assert result["routing_ok"] is True
    # ADR-0011 那张路由账本（profile 的 plobi/models.json）照旧落 DEFAULT_ROUTES——
    # 收口的是屏幕上那份 model 配置，不是路由账本。
    models = json.loads((profile_dir / "plobi" / "models.json").read_text(encoding="utf-8"))
    assert "l2_project" in models["roles"]

    # 控制组：同一条 spawn 路径在记录**确实**写了覆盖时照旧落笔。没有这一段，
    # 上面那句「没写」可能只是 spawn 根本不写 config 的假绿。
    controlled = _seed_profile_dir(
        spawn_env,
        "l2-ctrl",
        yaml.safe_dump({"model": dict(_CONFIGURED_DEFAULT)}, allow_unicode=True),
    )
    provider = _resolvable_provider_name()
    reg.upsert(
        _entry(
            name="ctrl",
            role="l2_planner",
            profile="l2-ctrl",
            provider=provider,
            model="a-chosen-model",
        )
    )
    reg.save()
    reg.spawn("ctrl")
    ctrl_cfg = yaml.safe_load((controlled / "config.yaml").read_text(encoding="utf-8"))
    assert ctrl_cfg["model"]["provider"] == provider
    assert ctrl_cfg["model"]["default"] == "a-chosen-model"


def test_agenda_template_carries_no_model_override(spawn_env, home):
    """日程秘书模板不许硬写 provider/model —— 它抄的是 DEFAULT_ROUTES 的 L2 兜底，
    落进 config.yaml 就是「用户没选过的提供商」+ 一个会当分组标题上屏的 aigw。
    """
    from plobi.agents.registry import AGENDA_TEMPLATE_PROFILE, agenda_template_entry

    template = agenda_template_entry()
    assert template.has_model_override is False, (template.provider, template.model)

    import yaml

    profile_dir = _seed_profile_dir(
        spawn_env,
        AGENDA_TEMPLATE_PROFILE,
        yaml.safe_dump({"model": dict(_CONFIGURED_DEFAULT)}, allow_unicode=True),
    )
    reg = AgentRegistry.load()
    reg.upsert(_entry(name="secretary", role="l1_secretary", provider="moonshot", model="kimi-k3"))
    reg.upsert(_entry(name=template.name, role=template.role, profile=template.profile,
                      provider="", model=""))
    reg.save()
    reg.spawn(template.name)

    text = (profile_dir / "config.yaml").read_text(encoding="utf-8")
    assert yaml.safe_load(text)["model"] == _CONFIGURED_DEFAULT
    assert "aigw" not in text and "workbuddy" not in text, text
    assert AgentEntry(name="Aura", role="l2_project").profile_name == "aura"
    assert AgentEntry(name="Default", role="l2_project").profile_name == "default"
    # 空值不许炸（未设 profile 的老行）
    assert AgentEntry(name="", role="l2_project").profile_name == ""
