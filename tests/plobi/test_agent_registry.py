"""B2: L2 常驻 Agent 注册表 — registry round-trip / routing 断言 / spawn。

注册表 = $PLOBI_HOME/plobi/projects.yaml（profile-safe）；spawn 落地 profile
（复用 plobi_cli.profiles.create_profile）+ 把路由写进**共享层那一份**
plobi/models.json（ADR-0011 断言绿）。裁 42 / 166（ADR-0013）之后 spawn
**不往分身家落任何配置**：模型与工作目录由名册那一行在读时叠
（``plobi.agents.registry.profile_config_overlay`` → ``load_config``）。
"""

from __future__ import annotations

import json
import os
import sys
import types
from pathlib import Path

import pytest
import yaml

from plobi.agents.registry import (
    UNBOUND_CATEGORIES,
    AgentEntry,
    AgentRegistry,
    RegistryError,
    default_path,
)


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
# 读链夹具（裁 42 / 166 / ADR-0013）：配置只有一份，分身侧的覆盖在读时叠
# ---------------------------------------------------------------------------


def _plobi_root(home) -> Path:
    """这些夹具里那**唯一的**顶层家（``home`` fixture 把 PLOBI_HOME 钉在这里）。"""
    return Path(home) / ".plobi"


def _top_level_config(home, model: dict) -> Path:
    """写那一份共享的 ``<root>/config.yaml`` 的 ``model:`` 节。

    这一格就是用户口中「我在设置里配一次，大家都能用」的落点：分身家不再有
    ``config.yaml``，所以「配一次」与「单独选」的差别只能在这里读出来。
    """
    path = _plobi_root(home) / "config.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump({"model": dict(model)}, allow_unicode=True), encoding="utf-8"
    )
    return path


def _effective_config(home, profile: str | None = None) -> dict:
    """那个家**实际读到的**那份配置——走被测代码自己的解析器，绝不手拼路径。

    ``profile=None`` 问的是顶层家自己（那时名册行覆盖整条不参与，见
    ``plobi_cli.config.agent_config_overlay``）。切换活跃家用
    ``set_plobi_home_override`` / ``reset_plobi_home_override``，前后各清一次
    ``load_config`` 的缓存——缓存签名带名册 mtime，但夹具要的是"这一次读的就是
    现在盘上这一份"，不留跨测试的陈旧展开。
    """
    from plobi_constants import reset_plobi_home_override, set_plobi_home_override
    from plobi_cli import config as config_mod

    target = _plobi_root(home) if profile is None else _plobi_root(home) / "profiles" / profile
    config_mod._LOAD_CONFIG_CACHE.clear()
    token = set_plobi_home_override(str(target))
    try:
        return config_mod.load_config()
    finally:
        reset_plobi_home_override(token)
        config_mod._LOAD_CONFIG_CACHE.clear()


# ---------------------------------------------------------------------------
# default_path & IO
# ---------------------------------------------------------------------------


def test_default_path_is_plobi_home_anchored(home):
    assert default_path() == Path(home) / ".plobi" / "plobi" / "projects.yaml"


def test_default_path_resolves_to_shared_root_in_profile_mode(tmp_path, monkeypatch):
    """裁定 42 / WP-PROFILE-SHARED-LAYER（R-096）：profile 活跃时名册也解析到 root 那一份。

    关系断言（路径同一性），不写体积/文件数快照——判据③ 的原话。分身侧故意放一份
    自己的副本：它必须被无视，而不是被优先（那就是 R-095 改名改不干净的根因）。
    """
    root = tmp_path / "plobiroot"
    profile = root / "profiles" / "aura"
    (profile / "plobi").mkdir(parents=True)
    (root / "plobi").mkdir(parents=True)
    (profile / "plobi" / "projects.yaml").write_text("agents: []\n", encoding="utf-8")

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("PLOBI_HOME", str(profile))
    monkeypatch.delenv("PLOBI_PROJECTS_CONFIG", raising=False)

    resolved = default_path()
    assert resolved == root / "plobi" / "projects.yaml"
    assert resolved != profile / "plobi" / "projects.yaml"


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


# ---------------------------------------------------------------------------
# WP-AGENT-CREATE-UPGRADE：多文件夹（用户 10-16「一个 Agent 肯定也要可以添加多个文件夹」）
# ---------------------------------------------------------------------------


def test_project_paths_roundtrip_keeps_primary_first(home):
    """清单存取 + 主文件夹语义：第一个是主文件夹（旧消费方读 project_path 不变）。"""
    reg = AgentRegistry.load()
    reg.upsert(
        _entry(name="multi", category="projects", project_paths=("D:/one", "D:/two", "D:/one"))
    )
    reloaded = AgentRegistry.load(reg.save())
    entry = reloaded.get("multi")
    assert entry.bound_paths() == ("D:/one", "D:/two")  # 去重保序
    assert entry.project_path == "D:/one"  # 主文件夹 = 第一个


def test_legacy_single_project_path_reads_as_one_tuple(home):
    """旧条目只有 project_path：读进来 = 一元清单，存盘不硬写新字段（旧形状原样）。"""
    reg = AgentRegistry.load()
    reg.upsert(_entry(name="legacy", category="projects", project_path="D:/only"))
    path = reg.save()
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert "project_paths" not in raw["agents"]["legacy"]  # 一元不写清单
    reloaded = AgentRegistry.load(path)
    assert reloaded.get("legacy").bound_paths() == ("D:/only",)


def test_courses_category_can_bind_folders(home):
    """用户 10-16：courses 移出 UNBOUND_CATEGORIES——课程 Agent 可绑文件夹。"""
    assert "courses" not in UNBOUND_CATEGORIES
    reg = AgentRegistry.load()
    entry = reg.upsert(
        _entry(name="c1", category="courses", project_path="D:/Cloud/Courses/x")
    )
    assert entry.bound_paths() == ("D:/Cloud/Courses/x",)
    reloaded = AgentRegistry.load(reg.save())
    assert reloaded.get("c1").category == "courses"
    assert reloaded.get("c1").bound_paths() == ("D:/Cloud/Courses/x",)


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

    spawn 在测试里必须验证 profile 目录 + 那张共享的路由表；**家里不该出现
    ``config.yaml``**（以前这一句替它造一份，正是本刀关掉的那扇门）。
    create_profile 会走 seed skills / clone 等重逻辑，直接替换成 mkdir 即可。
    """
    import plobi_cli.profiles as profiles_mod

    def fake_create_profile(name, **kwargs):
        profile_dir = Path(home) / ".plobi" / "profiles" / name
        profile_dir.mkdir(parents=True, exist_ok=True)
        (profile_dir / "plobi").mkdir(exist_ok=True)
        return profile_dir

    def fake_profile_exists(name):
        return (Path(home) / ".plobi" / "profiles" / name).is_dir()

    monkeypatch.setattr(profiles_mod, "create_profile", fake_create_profile)
    monkeypatch.setattr(profiles_mod, "profile_exists", fake_profile_exists)
    monkeypatch.setattr("plobi_cli.profiles.create_profile", fake_create_profile)
    monkeypatch.setattr("plobi_cli.profiles.profile_exists", fake_profile_exists)
    return home


def test_spawn_creates_profile_and_models(spawn_env):
    """路由表只存共享层那一份；**分身家里不许再落复印件**（裁定 166 决定一，判据①）。

    这一条以前断言的是 ``<profile>/plobi/models.json`` 存在——那一行就是现核
    「分身 models.json 23 份、去重后内容只有 1 份」的来源：``spawn`` 每次都往那个家整表复印一遍，
    而顶层反倒没有那一份（``$PLOBI_HOME/plobi/models.json`` 不存在），于是「配一次模型大家能用」
    在机制上不可能成立。改成断言共享那一份存在且生效 + 分身家没有复印件。
    配置文件同理：这一行声明的模型覆盖**在读时叠**，家里不落 ``config.yaml``。
    """
    reg = AgentRegistry.load()
    reg.upsert(_entry(name="secretary", role="l1_secretary", provider="moonshot", model="kimi-k3"))
    reg.upsert(_entry(name="agenda", role="l2_agenda", provider="deepseek", model="deepseek-chat"))
    reg.save()

    _top_level_config(spawn_env, dict(_CONFIGURED_DEFAULT))
    result = reg.spawn("agenda")

    assert result["routing_ok"] is True
    profile_dir = Path(result["profile_dir"])
    assert profile_dir.is_dir()

    from plobi.routing import ModelRouter
    from plobi.routing.models import shared_config_path

    shared = shared_config_path()
    models = json.loads(shared.read_text(encoding="utf-8"))
    # 裁 172：那张表是「这一类默认用什么」，**不接收任何一行的覆盖**（名册 24 行里
    # l2_project 占 23 行，折进去就是给一个 Agent 单选 = 整类一起变）。这一行自己的生效值
    # 问 ``route_for``——它仍是 deepseek/deepseek-chat，但只属于 agenda 那一行。
    assert models["roles"]["l2_agenda"]["provider"] == "aigw"
    assert models["roles"]["l2_agenda"]["model"] == "workbuddy/deepseek-chat"
    agenda_route = reg.route_for(reg.get("agenda"))
    assert (agenda_route.provider, agenda_route.model) == ("deepseek", "deepseek-chat")
    # ADR-0011：那张唯一的表断言绿（L1≠L2）
    assert ModelRouter.load(shared).violations() == []
    # 判据①：这个家不许带路由表复印件
    assert not (profile_dir / "plobi" / "models.json").exists(), "spawn 又把整张表复印进了分身家"
    # 判据①的另一半：也不许带配置文件复印件
    assert not (profile_dir / "config.yaml").exists(), "spawn 又往分身家落了一份 config.yaml"
    # 名册行上的覆盖仍生效——读时叠：只有这个分身读到它自己那一行，顶层照旧
    profile_model = _effective_config(spawn_env, result["profile"])["model"]
    assert profile_model["provider"] == "deepseek"
    assert profile_model["default"] == "deepseek-chat"
    assert _effective_config(spawn_env)["model"] == _CONFIGURED_DEFAULT


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
    """WP-STUDIO-CWD / 裁定 36.1 仍跑得动——display_name 不影响工作目录绑定。

    这条是裁定 37.1 / 39 显式要求的回归：确保本刀没碰 cwd 逻辑。机制换了（裁 42 /
    166）：那一格不再往分身家写 ``terminal.cwd``，而是**读时**从名册那一行的
    ``project_path`` 取（:func:`plobi.agents.registry.project_cwd_for_profile`），
    所以这里问的对象换成那条读链——绑定仍要落得出那个目录，而家里不许多出
    一份 ``config.yaml``。走 stub 后的 ``AgentRegistry.spawn``（不走 _stub_spawn）。
    """
    from plobi.agents import registry as registry_mod

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
    profile_dir = _seed_profile_dir(home, "l2-plobi")

    reg = AgentRegistry.load()
    reg.upsert(
        AgentEntry(
            name="Plobi",
            role="l2_project",
            profile="l2-plobi",
            model="deepseek-chat",
            project_path=str(project_dir),
            category="projects",
            display_name="Plobi",  # 本刀新增字段；工作目录仍要跟着这一行。
        )
    )
    # 读时叠的前提是那一行**在盘上**（旧机制由 spawn 落文件，所以以前这里不用 save）。
    reg.save()
    result = reg.spawn("Plobi")

    assert result["profile"] == "l2-plobi"
    assert registry_mod.project_cwd_for_profile("l2-plobi") == str(project_dir.absolute())
    # 生效链上同样读得到（不只那个查询函数）：名册行覆盖 → 顶层 → 出厂默认。
    _top_level_config(home, dict(_CONFIGURED_DEFAULT))
    assert _effective_config(home, "l2-plobi")["terminal"]["cwd"] == str(
        project_dir.absolute()
    )
    assert not (profile_dir / "config.yaml").exists(), "spawn 又往分身家落了一份 config.yaml"


# ---------------------------------------------------------------------------
# 可解析闸门：底座认不出来的 provider **不进那一个家的生效 model 节**
# （症状：桌面点进分身 → 「Unknown provider 'aigw'. Check 'plobi model' …」）
# 闸门原样还在（:func:`plobi.agents.registry.profile_config_overlay`），只是位置从
# "spawn 落盘那一节" 挪到了"读时叠这一节"，所以②那条的 warning 要从一次读取里收。
# ---------------------------------------------------------------------------

_UNRESOLVABLE_PROVIDER = "no-such-provider-gate-check"


def _resolvable_provider_name() -> str:
    """从底座那个权威自己的注册表里取一个它认的名字（不写死 provider 名单）。"""
    from plobi_cli.auth import PROVIDER_REGISTRY, resolve_provider

    assert PROVIDER_REGISTRY, "底座注册表为空 —— 闸门无从验证"
    name = sorted(PROVIDER_REGISTRY)[0]
    assert resolve_provider(name) == name
    return name


def _seed_profile_dir(home, profile: str) -> Path:
    """预建 profile 目录 + ``plobi/`` 子目录（spawn 见目录已存在就直接用）。

    家里**不写 ``config.yaml``**：本刀关掉的就是这一格——生效配置只读共享根那一份
    ＋名册行的读时覆盖，家里那份复印件既不该新增也不该被读。
    """
    profile_dir = Path(home) / ".plobi" / "profiles" / profile
    profile_dir.mkdir(parents=True, exist_ok=True)
    (profile_dir / "plobi").mkdir(exist_ok=True)
    return profile_dir


def test_spawn_writes_model_section_when_provider_resolves(spawn_env, home):
    """① 名册行声明了可解析的 provider/model ⇒ **只有那个分身**的生效模型变成它选的值。

    落盘的 ``model:`` 节改成了读时叠（:func:`profile_config_overlay`），所以"写这一格"
    现在的形状是：那个家读到名册行，其他家（含顶层）继续读「配一次」的那一份。
    """
    provider = _resolvable_provider_name()
    _top_level_config(home, dict(_CONFIGURED_DEFAULT))
    reg = AgentRegistry.load()
    reg.upsert(
        _entry(name="secretary", role="l1_secretary", provider="moonshot", model="kimi-k3")
    )
    reg.upsert(
        _entry(name="proj", role="l2_project", profile="l2-proj", provider=provider, model="a-model")
    )
    reg.save()

    result = reg.spawn("proj")

    profile_dir = Path(result["profile_dir"])
    assert not (profile_dir / "config.yaml").exists(), "spawn 又往分身家落了一份 config.yaml"
    model = _effective_config(home, result["profile"])["model"]
    assert model["provider"] == provider
    assert model["default"] == "a-model"
    # 「配一次大家用」：顶层那一份没被这一行带跑。
    assert _effective_config(home)["model"] == _CONFIGURED_DEFAULT


def test_spawn_skips_unresolvable_provider_without_failing(spawn_env, home, caplog):
    """② provider 不可解析 ⇒ 那一行不进生效模型、spawn 照常返回、留一条点名 warning。

    「不写」现在长得像"这个家没拿到覆盖"：它的生效模型就是顶层那一份（继承）。
    warning 也换了发出处——闸门在**读时**那道闸上（``profile_config_overlay``），
    所以这一条把"读一次生效配置"放进 caplog 的窗口里。
    """
    import logging
    from plobi_cli.auth import AuthError, resolve_provider

    # 前提自证（不是写死的名字表）：底座权威确实拒这个名字，红条就是它抛的。
    with pytest.raises(AuthError) as excinfo:
        resolve_provider(_UNRESOLVABLE_PROVIDER)
    assert excinfo.value.code == "invalid_provider"

    _top_level_config(home, dict(_CONFIGURED_DEFAULT))
    profile_dir = _seed_profile_dir(spawn_env, "l2-proj")
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
        profile_cfg = _effective_config(home, "l2-proj")

    assert result["routing_ok"] is True
    assert Path(result["profile_dir"]) == profile_dir
    assert not (profile_dir / "config.yaml").exists()
    assert profile_cfg["model"] == _CONFIGURED_DEFAULT, "不可解析的 provider 进了生效模型"
    # 原「跳过 model 节不许动 config 的其他键」的新形状：那一行被闸门拒掉之后，这个家
    # 读到的**整份**配置与顶层逐字相同——覆盖没进去，也没有第二个键被顺手改掉。
    assert profile_cfg == _effective_config(home)
    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any(_UNRESOLVABLE_PROVIDER in m for m in warnings), warnings
    assert any("plobi model" in m for m in warnings), warnings


def test_unresolvable_provider_skip_leaves_terminal_cwd_and_inherited_model(spawn_env, home):
    """③ 跳过模型覆盖不影响 terminal.cwd；那个家继承的是顶层那一份。

    cwd 与模型是两件事（WP-STUDIO-CWD / 裁定 36.1 已拆开）：模型不可解析不许把
    工作目录一起带走；而"不给覆盖"的语义正是**继承**——生效值回到「配一次」那一份，
    不是回落到出厂默认，也不是读家里那份根本不存在的复印件。
    """
    _top_level_config(home, dict(_CONFIGURED_DEFAULT))
    project_dir = Path(home) / "workspace"
    project_dir.mkdir(parents=True, exist_ok=True)
    profile_dir = _seed_profile_dir(spawn_env, "l2-proj")

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

    cfg = _effective_config(home, result["profile"])
    assert cfg["terminal"]["cwd"] == str(project_dir.absolute())
    assert cfg["model"] == _effective_config(home)["model"] == _CONFIGURED_DEFAULT
    assert not (profile_dir / "config.yaml").exists()
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
    """注册表那条记录没写 provider/model 时，那个分身生效的模型＝顶层「配一次」那一份。

    反例（本次修掉的病症）：新落的 Framelet / Plobi 两条被盖成
    ``provider: aigw`` + ``default: workbuddy/deepseek-chat``，而 ``base_url`` 还是
    克隆来的 minimax —— 一个自相矛盾的三元组，用户从没选过它。它来自
    :data:`plobi.routing.models.DEFAULT_ROUTES` 的硬编码兜底：省略覆盖被当成了
    「用默认路由」，而那个默认路由和「用户当前配置的默认模型」不是一回事
    （``e9b6034`` 把 aigw 注册成真 provider 后，可解析闸门也不再拦它）。
    机制换了（裁 166 / ADR-0013）之后这一格是**读时**判的：行上没声明 ⇒ 整节覆盖都不给，
    于是生效值逐字等于顶层那一份，而不是"某处落盘的值没被改写"。
    """
    _top_level_config(home, dict(_CONFIGURED_DEFAULT))
    profile_dir = _seed_profile_dir(spawn_env, "l2-proj")
    reg = AgentRegistry.load()
    reg.upsert(_entry(name="secretary", role="l1_secretary", provider="moonshot", model="kimi-k3"))
    reg.upsert(
        _entry(name="proj", role="l2_project", profile="l2-proj", provider="", model="")
    )
    reg.save()

    result = reg.spawn("proj")

    model = _effective_config(home, result["profile"])["model"]
    assert model == _CONFIGURED_DEFAULT, "分身被盖上了它没被选择的模型"
    rendered = json.dumps(model, ensure_ascii=False)
    assert "aigw" not in rendered, rendered
    assert "workbuddy" not in rendered, rendered
    assert result["routing_ok"] is True
    # 家里既没有配置文件、也没有路由表复印件（两样都只有共享层那一份）。
    assert not (profile_dir / "config.yaml").exists(), "spawn 又往分身家落了一份 config.yaml"
    # ADR-0011 那张路由账本照旧落 DEFAULT_ROUTES——收口的是屏幕上那份 model 配置，不是路由账本。
    # 落点按裁定 166 决定一改成**共享层那一份**（以前这里读的是分身家的复印件，23 份内容全同）。
    from plobi.routing.models import shared_config_path

    models = json.loads(shared_config_path().read_text(encoding="utf-8"))
    assert "l2_project" in models["roles"]
    assert not (profile_dir / "plobi" / "models.json").exists(), "spawn 又往分身家复印了一张表"

    # 控制组：同一条读链在记录**确实**写了覆盖时必须落到那个分身。没有这一段，
    # 上面那句「没写」可能只是覆盖整条根本不生效的假绿。
    _seed_profile_dir(spawn_env, "l2-ctrl")
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
    ctrl_model = _effective_config(home, "l2-ctrl")["model"]
    assert ctrl_model["provider"] == provider
    assert ctrl_model["default"] == "a-chosen-model"
    # 而且只有它变：顶层那一份仍是用户配的那一个，别人没被这一行带跑。
    assert _effective_config(home)["model"] == _CONFIGURED_DEFAULT


def test_agenda_template_carries_no_model_override(spawn_env, home):
    """日程秘书模板不许硬写 provider/model —— 它抄的是 DEFAULT_ROUTES 的 L2 兜底，
    落进那唯一一份配置就是「用户没选过的提供商」+ 一个会当分组标题上屏的 aigw。
    """
    from plobi.agents.registry import AGENDA_TEMPLATE_PROFILE, agenda_template_entry

    template = agenda_template_entry()
    assert template.has_model_override is False, (template.provider, template.model)

    _top_level_config(home, dict(_CONFIGURED_DEFAULT))
    profile_dir = _seed_profile_dir(spawn_env, AGENDA_TEMPLATE_PROFILE)
    reg = AgentRegistry.load()
    reg.upsert(_entry(name="secretary", role="l1_secretary", provider="moonshot", model="kimi-k3"))
    reg.upsert(_entry(name=template.name, role=template.role, profile=template.profile,
                      provider="", model=""))
    reg.save()
    reg.spawn(template.name)

    model = _effective_config(home, AGENDA_TEMPLATE_PROFILE)["model"]
    assert model == _CONFIGURED_DEFAULT
    rendered = json.dumps(model, ensure_ascii=False)
    assert "aigw" not in rendered and "workbuddy" not in rendered, rendered
    assert not (profile_dir / "config.yaml").exists(), "spawn 又往分身家落了一份 config.yaml"
    assert AgentEntry(name="Aura", role="l2_project").profile_name == "aura"
    assert AgentEntry(name="Default", role="l2_project").profile_name == "default"
    # 空值不许炸（未设 profile 的老行）
    assert AgentEntry(name="", role="l2_project").profile_name == ""
