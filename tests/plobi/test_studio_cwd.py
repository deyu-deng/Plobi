"""项目分身的工作目录：真源是名册那一行，读时叠，不落文件（裁 36.1 的契约，机制已换）。

**这把刀换了机制**（裁 42 / 裁 166 / ADR-0013 ``config.yaml`` 那一行）。原来这一族钉的是
``apply_l2_project_cwd`` 往分身家 ``config.yaml`` 写一节 ``terminal.cwd``——那等于每建一个
项目分身就多一份复印件，而那份复印件恰恰是「一个模型要配 24 遍」的载体。现在那一格的
真源是名册那一行的 ``project_path``，由
:func:`plobi.agents.registry.profile_config_overlay` 在 :func:`plobi_cli.config.load_config`
里**读时叠**（顺序＝名册行覆盖 → 顶层 → 出厂默认），落盘那扇门（``apply_l2_project_cwd``
与 ``_write_profile_config``）已经关掉。

契约本身一个字没改，所以测试也照原来的五条写，只是问的对象换了：
① 带 ``project_path`` 的项目分身，工作目录＝那一行那个文件夹；
② 目录不存在**绝不编造**（原来那句「NEVER fabricate a cwd」搬进
   :func:`plobi.agents.registry._resolvable_project_cwd`）；
③ 空 path / agenda / butler 那些行不给 cwd（它们本来就没有文件夹绑定）；
④ 改名册那一行当场生效，不用重启进程（覆盖是读时算的，缓存签名必须带名册的 mtime——
   这一条是钉「别把覆盖烘进缓存」那颗钉子）；
⑤ 分身家**不再因为 spawn 多出 ``config.yaml``**（ADR-0013 完成定义第 2 条的形状）。

外加原来那条 L2-DIET 与 cwd 互不越界的纪律：减肥动的是工具集，不许把工作目录挪走。
"""

from __future__ import annotations

import os
import sys
import types
from pathlib import Path

import pytest
import yaml


def _stub_plobi_profiles(monkeypatch, home: Path):
    """Stub the ``plobi_cli.agent_identity`` helpers used by ``AgentRegistry.spawn``."""
    fake = types.ModuleType("plobi_cli.agent_identity")
    fake.create_profile = lambda name, **kw: home / ".plobi" / "profiles" / name
    fake.get_profile_dir = lambda name: home / ".plobi" / "profiles" / name
    fake.profile_exists = lambda name: (home / ".plobi" / "profiles" / name).is_dir()
    monkeypatch.setitem(sys.modules, "plobi_cli.agent_identity", fake)


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """一个顶层家 + 一份名册，两个落点都用环境变量钉死（不碰真机那份）。"""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    plobi = tmp_path / ".plobi"
    (plobi / "plobi").mkdir(parents=True)
    monkeypatch.setenv("PLOBI_HOME", str(plobi))
    monkeypatch.setenv("PLOBI_PROJECTS_CONFIG", str(plobi / "plobi" / "projects.yaml"))
    monkeypatch.delenv("PLOBI_MODELS_CONFIG", raising=False)
    # 顶层那一份：用户「配一次」的那个值住在这里。
    (plobi / "config.yaml").write_text(
        "model:\n  provider: aigw\n  default: workbuddy/top-one\n", encoding="utf-8"
    )
    from plobi_cli import config as cfg

    cfg._LOAD_CONFIG_CACHE.clear()
    _stub_plobi_profiles(monkeypatch, tmp_path)
    yield tmp_path
    cfg._LOAD_CONFIG_CACHE.clear()


def _roster(rows: dict) -> None:
    from plobi.agents.registry import REGISTRY_VERSION

    payload = {"version": REGISTRY_VERSION, "agents": rows}
    Path(os.environ["PLOBI_PROJECTS_CONFIG"]).write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )


def _effective(agent_id: str) -> dict:
    """**这个身份**实际读到的配置——走被测代码自己的解析函数，不手拼路径。

    2026-11-05 之后「哪个 Agent」由 ``PLOBI_AGENT_ID``（名册 ``name``）决定，不再由
    ``PLOBI_HOME`` 指进某个 ``profiles/<slug>`` 子家决定：家就是共享根，身份是环境变量。
    所以这里钉 env，不钉家。
    """
    from plobi_cli import config as cfg

    if agent_id:
        os.environ["PLOBI_AGENT_ID"] = agent_id
    else:
        os.environ.pop("PLOBI_AGENT_ID", None)
    cfg._LOAD_CONFIG_CACHE.clear()
    return cfg.load_config()


def _project_row(name: str, profile: str, path) -> dict:
    return {
        "name": name,
        "role": "l2_project",
        "profile": profile,
        "category": "projects",
        "project_path": str(path) if path is not None else "",
    }


# ---------------------------------------------------------------------------
# ① / ② / ③ 工作目录从名册那一行读出来
# ---------------------------------------------------------------------------


def test_a_project_agent_reads_its_workspace_from_the_roster_row(home):
    """契约①：名册行有 ``project_path`` ⇒ 那个家的 ``terminal.cwd`` 就是它。"""
    workspace = home / "workspace"
    workspace.mkdir()
    profile_dir = home / ".plobi" / "profiles" / "l2-plobi"
    profile_dir.mkdir(parents=True)
    _roster({"Plobi": _project_row("Plobi", "l2-plobi", workspace)})

    cfg = _effective("Plobi")

    assert cfg["terminal"]["cwd"] == str(workspace.absolute())


def test_the_roster_wins_over_the_top_level_file(home):
    """链条是「名册行覆盖 → 顶层」：顶层那份没有 cwd，名册那行有 ⇒ 名册行赢。

    反过来说明这一格不是"读到默认值就算了"：``DEFAULT_CONFIG`` 的 cwd 是 ``.``，
    读到这里成了真实目录，才叫覆盖。
    """
    workspace = home / "wins"
    workspace.mkdir()
    profile_dir = home / ".plobi" / "profiles" / "l2-wins"
    profile_dir.mkdir(parents=True)
    _roster({"Wins": _project_row("Wins", "l2-wins", workspace)})

    assert _effective("Wins")["terminal"]["cwd"] != "."


def test_a_dead_project_path_never_becomes_a_cwd(home):
    """契约②：那一行的目录不存在 ⇒ 不给 cwd，也不把一个假路径塞进会话。

    原话是 ``apply_l2_project_cwd`` 的「NEVER fabricate a cwd」——那句话跟着机制一起
    搬进了 :func:`plobi.agents.registry._resolvable_project_cwd`，这条是它的替身。
    """
    ghost = home / "no-such-folder"
    profile_dir = home / ".plobi" / "profiles" / "l2-ghost"
    profile_dir.mkdir(parents=True)
    _roster({"Ghost": _project_row("Ghost", "l2-ghost", ghost)})

    cfg = _effective("Ghost")

    assert not ghost.exists()
    assert cfg["terminal"]["cwd"] != str(ghost)


@pytest.mark.parametrize("role", ["l2_agenda", "l2_butler"])
def test_butler_and_agenda_rows_get_no_cwd(home, role):
    """契约③：秘书 / 日程那一行没有文件夹绑定 ⇒ 不许把它们按进某个项目的目录。"""
    profile_dir = home / ".plobi" / "profiles" / "l2-agenda"
    profile_dir.mkdir(parents=True)
    _roster({
        "agenda": {
            "name": "agenda",
            "role": role,
            "profile": "l2-agenda",
            "category": "butler",
            "project_path": "",
        }
    })

    assert _effective("agenda")["terminal"]["cwd"] == "."


# ---------------------------------------------------------------------------
# ④ 改名册那一行当场生效（钉住缓存签名里那颗名册 mtime）
# ---------------------------------------------------------------------------


def test_moving_the_folder_in_the_roster_moves_the_agent_without_a_restart(home):
    """契约④：进程内改名册那一行 ⇒ 下一次 ``load_config`` 就是新目录。

    覆盖是读时叠的，而 ``load_config`` 那套缓存按文件签名判命中。名册那份签名一旦漏掉，
    「给这个项目换个文件夹」就要等后端重启才生效——这条就是不让那颗钉子再漏一次。
    """
    first = home / "first"
    second = home / "second"
    first.mkdir()
    second.mkdir()
    profile_dir = home / ".plobi" / "profiles" / "l2-move"
    profile_dir.mkdir(parents=True)
    _roster({"Move": _project_row("Move", "l2-move", first)})

    assert _effective("Move")["terminal"]["cwd"] == str(first.absolute())

    # 不重启进程、不清缓存：只改名册那一行。
    from plobi_cli import config as cfg

    rows = yaml.safe_load(
        Path(os.environ["PLOBI_PROJECTS_CONFIG"]).read_text(encoding="utf-8")
    )["agents"]
    rows["Move"]["project_path"] = str(second)
    _roster(rows)

    assert cfg.load_config()["terminal"]["cwd"] == str(second.absolute())


# ---------------------------------------------------------------------------
# ⑤ spawn 不再在家下落那份文件（外加"复印件翻不回第二真源"那条）
# ---------------------------------------------------------------------------


def test_spawning_a_project_agent_leaves_no_config_yaml_in_its_home(home):
    """契约⑤＝ADR-0013 完成定义第 2 条：新建那个家里没有 ``config.yaml``。

    ``spawn`` 这一条腿以前会写两节（``model:`` 与 ``terminal.cwd:``），现在一节都不写。
    工作目录照样生效（契约①那条已证），所以这里要钉的是**没有文件**这件事本身。
    """
    from plobi.agents.registry import AgentEntry, AgentRegistry

    workspace = home / "spawned"
    workspace.mkdir()
    reg = AgentRegistry.load()
    reg.upsert(
        AgentEntry(
            name="Spawned",
            role="l2_project",
            project_path=str(workspace),
            category="projects",
        )
    )
    top_config = home / ".plobi" / "config.yaml"

    result = reg.spawn("Spawned")

    # B 刀之后 spawn 不再给这行分配家：返回值里连 ``profile_dir`` 这个键都没有，
    # 顶层那一份也不多出 ``terminal:``（cwd 是读时叠的，不落盘）、模型仍是顶层原值。
    assert "profile_dir" not in result
    after = yaml.safe_load(top_config.read_text(encoding="utf-8")) or {}
    assert "terminal" not in after
    assert (after.get("model") or {}).get("default") == "workbuddy/top-one"


def test_a_stale_copy_left_in_the_home_is_not_read(home):
    """复印件翻不回第二真源：家里就算躺着一份 ``config.yaml``，生效值也不来自它。

    这台机器存量那 24 份就是这么赢的（自家优先读），所以"隔离掉"之外还要一条
    「就算没隔离干净也不读」。这一条同时护住 ``_config_write_path`` 那道改道——
    某个上游开关万一又在家下重建一份，它也进不了生效值。
    """
    workspace = home / "real-folder"
    workspace.mkdir()
    profile_dir = home / ".plobi" / "profiles" / "l2-stale"
    profile_dir.mkdir(parents=True)
    (profile_dir / "config.yaml").write_text(
        "model:\n  provider: copyist\n  default: stale/copy\n"
        "terminal:\n  cwd: " + str(home / "wrong-folder") + "\n",
        encoding="utf-8",
    )
    _roster({"Stale": _project_row("Stale", "l2-stale", workspace)})

    cfg = _effective("Stale")

    assert cfg["terminal"]["cwd"] == str(workspace.absolute())
    assert cfg["model"]["default"] == "workbuddy/top-one"
    # 那份复印件本身一个字不动（隔离是"移走"，不是"改写"）。
    assert "stale/copy" in (profile_dir / "config.yaml").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 工具集减肥不许顺手挪走工作目录（原来那条 L2-DIET 边界）
# ---------------------------------------------------------------------------


def test_the_l2_diet_cannot_move_an_agents_workspace(home):
    """裁 36.1 与裁 33.3 各管一件事：减肥剥工具集，``project_path`` 才是工作目录。

    以前这两件事都落在同一个文件里，所以那条「diet 不许动 terminal.cwd」是靠比对
    文件内容证的。现在 diet 写的还是分身家那份（它已经不存在 ⇒ 静默 no-op），
    而生效的 cwd 来自名册那一行——所以这一条改成证「diet 跑完之后，工作目录照旧」。
    """
    from plobi.agents.registry import apply_l2_project_diet

    workspace = home / "dieted"
    workspace.mkdir()
    profile_dir = home / ".plobi" / "profiles" / "l2-diet"
    profile_dir.mkdir(parents=True)
    _roster({"Diet": _project_row("Diet", "l2-diet", workspace)})
    before = _effective("Diet")["terminal"]["cwd"]

    apply_l2_project_diet(profile_dir)

    assert (
        _effective("Diet")["terminal"]["cwd"]
        == before
        == str(workspace.absolute())
    )
