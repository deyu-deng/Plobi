# -*- coding: utf-8 -*-
"""用户看到的那几个面，身份判据要问名册那一行（裁 192）。

家目录退休之后（裁 42／178 乙案；10-24 隔离搬走 23 具家、10-27 用户签字把隔离区删干净），
这些面全部开始说假话：

* ``plobi profile list`` 只剩 ``default`` 一行（目录扫描）；
* ``plobi profile show <名字>`` 报「does not exist」；
* ``plobi profile use <名字>`` 报「does not exist」，而且提示指向**已封**的
  ``plobi profile create``（10-25 起那条命令退 2）；
* ``plobi profile describe <名字>`` 读不到、写不进（描述住在 ``<家>/profile.yaml``）；
* ``plobi profile retire <名字>`` 报「does not exist」（以前是往家目录里盖 ``.retired``）；
* ``plobi profile import`` / ``install`` 还能把 ``profiles/<名字>/`` 造出来——那是仅存的
  两个建家入口，家随时能回来。

这一档把上面六条钉住。
"""

import sys
from pathlib import Path

import pytest

from plobi_cli import agent_identity as profiles_mod


ROSTER_YAML = (
    "agents:\n"
    "  Aura:\n"
    "    role: l2_project\n"
    "    profile: aura\n"
    "    description: 名册上的那一行\n"
    "  agenda:\n"
    "    role: l2_agenda\n"
    "    description: 日程秘书\n"
)


@pytest.fixture()
def roster_home(tmp_path, monkeypatch):
    """隔离 PLOBI_HOME ＋ 一份名册；家目录一个都没有（这正是退役后的形状）。"""
    home = tmp_path / ".plobi"
    (home / "plobi").mkdir(parents=True)
    (home / "profiles").mkdir()
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("PLOBI_HOME", str(home))
    roster = home / "plobi" / "projects.yaml"
    roster.write_text(ROSTER_YAML, encoding="utf-8")
    monkeypatch.setenv("PLOBI_PROJECTS_CONFIG", str(roster))
    return home


# ── 名单 ────────────────────────────────────────────────────────────────

def test_identity_list_shows_the_roster(roster_home):
    """用户看到的那份名单＝名册上的每一行 ＋ default（不是目录扫描）。"""
    names = [p.name for p in profiles_mod.list_identity_profiles()]
    assert "default" in names
    assert "Aura" in names
    assert "agenda" in names
    # 目录扫描那一支现在只剩 default —— 两支不是一回事
    assert [p.name for p in profiles_mod.list_profiles()] == ["default"]


def test_identity_list_carries_the_roster_description(roster_home):
    """名单上带的就是名册行里写的那句描述。"""
    by_name = {p.name: p for p in profiles_mod.list_identity_profiles()}
    assert by_name["Aura"].description == "名册上的那一行"


# ── 描述：落点搬到名册行 ────────────────────────────────────────────────

def test_description_round_trips_on_the_roster_row(roster_home):
    """描述写在名册那一行上，读得回来。"""
    assert profiles_mod.read_identity_description("Aura") == ("名册上的那一行", False)
    assert profiles_mod.write_identity_description("Aura", "改过的描述") is True
    desc, auto = profiles_mod.read_identity_description("Aura")
    assert desc == "改过的描述"
    assert auto is False


def test_description_auto_flag_survives(roster_home):
    """「这段是自动生成的」那一格也跟着走（自动描述器要靠它保护用户手写的那段）。"""
    profiles_mod.write_identity_description("Aura", "机器写的", description_auto=True)
    assert profiles_mod.read_identity_description("Aura") == ("机器写的", True)


def test_description_of_unknown_name_is_empty(roster_home):
    """名册上没有的名字：读回来是空串，写回去是 False（不静默）。"""
    assert profiles_mod.read_identity_description("no-such-agent") == ("", False)
    assert profiles_mod.write_identity_description("no-such-agent", "x") is False


# ── 退役 ────────────────────────────────────────────────────────────────

def test_retire_archives_the_roster_row(roster_home):
    """退役＝名册行上那一格 archived（家没了，那枚 `.retired` 章没地方盖）。"""
    assert profiles_mod.archive_identity("Aura", archived=True) is True
    assert "Aura" not in [a["name"] for a in profiles_mod.list_roster_agents()]
    # 归档行仍留在名册里（裁定 19），只是从默认名单里藏起来
    retired = {p.name: p for p in profiles_mod.list_identity_profiles()}
    assert retired["Aura"].retired is True
    # 已经在那个状态了：第二次不改，返回 False
    assert profiles_mod.archive_identity("Aura", archived=True) is False


def test_active_profile_accepts_a_roster_row(roster_home):
    """``plobi profile use <名字>`` 不该说「不存在」，更不该指向已封的 create。"""
    profiles_mod.set_active_profile("Aura")
    assert (roster_home / "active_profile").exists()
    with pytest.raises(FileNotFoundError) as exc:
        profiles_mod.set_active_profile("no-such-agent")
    assert "profile create" not in str(exc.value)


# ── 仅存的两个建家入口 ──────────────────────────────────────────────────

@pytest.mark.parametrize("argv_tail", [
    ["profile", "import", "dummy.tar.gz"],
    ["profile", "install", "https://example.invalid/dist.zip"],
])
def test_build_home_entries_are_closed(roster_home, monkeypatch, argv_tail):
    """``import`` / ``install`` 都退 2，且**不造家**。"""
    from plobi_cli import main as main_mod

    monkeypatch.setattr(sys, "argv", ["plobi"] + argv_tail)
    with pytest.raises(SystemExit) as exc:
        main_mod.main()
    assert exc.value.code == 2
    assert list((roster_home / "profiles").iterdir()) == []


def test_the_library_entries_are_closed_too(roster_home):
    """**库函数本体**也退（2026-11-06 补）。

    ``import_profile``／``export_profile`` 只操作「家」：导入那一个会把
    ``shutil.move`` 出 ``profiles/<名>/``。CLI 的两个口 10-27 退了 2，但这两个库函数
    还活着——谁从代码里一调就把分身概念复活，而那正是 10-24／10-25 反复清掉的东西。
    """
    with pytest.raises(RuntimeError) as exc:
        profiles_mod.import_profile("dummy.tar.gz", name="ghost")
    assert "退役" in str(exc.value)

    with pytest.raises(RuntimeError) as exc:
        profiles_mod.export_profile("ghost", str(roster_home.parent / "out.tar.gz"))
    assert "退役" in str(exc.value)

    assert list((roster_home / "profiles").iterdir()) == []

