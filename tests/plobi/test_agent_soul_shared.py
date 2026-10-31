"""WP-AGENT-SOUL-SHARED —— L2 身份的承载从「一整个家」搬到共享层一份。

**为什么有这一刀**（侦察结论，不是猜的）：`spawn()` 四件事里
`_write_profile_models` 早已只写共享层（`profile_dir` 参数是摆设），真正还靠
「家」承重的只剩**身份**——写侧 `ensure_l2_identity_soul` 写 `home/SOUL.md`，
读侧 `agent/prompt_builder.load_soul_md()` 只读 `get_plobi_home()/SOUL.md`。
若先做「spawn 不再建家」，24 个 L2 会一起丢掉身份（系统提示里那个身份槽直接空掉）。
所以顺序只能是：**A 身份搬家 → B 不再建家 → C 工具集 diet**。

**落点**：共享层下 `souls/<名册 name>.md`（`get_shared_plobi_root()`，与
`shared_config_path()` 同一个 root）。键用**名册 name**——读侧手里只有
`PLOBI_AGENT_ID`（`build_child_command` 放的就是 `entry.name`），两侧同一个键，
读侧不必反查名册。

这四条就是防 10-23 11:36 那次事故复发的牙：那次是把「行能不能点」绑在
「盘上有没有家」上，一次数据搬家把整条左栏打瘫（现读 0/0/24）。身份不能再犯。
"""

from __future__ import annotations

import pytest

from plobi.agents.registry import (
    AgentEntry,
    ensure_l2_identity_soul,
    shared_soul_path_for,
    shared_souls_root,
)


@pytest.fixture()
def soul_home(tmp_path, monkeypatch):
    """把共享层 root 指到 tmp。

    `get_shared_plobi_root()` 走 `get_plobi_home()` 的同一套覆盖链，所以钉
    `PLOBI_HOME` 就够；`PLOBI_AGENT_ID` 一律先摘掉，免得宿主环境渗进来。
    """
    home = tmp_path / "plobi-home"
    (home / "plobi").mkdir(parents=True)
    monkeypatch.setenv("PLOBI_HOME", str(home))
    monkeypatch.delenv("PLOBI_AGENT_ID", raising=False)
    monkeypatch.delenv("PLOBI_STATE_HOME", raising=False)
    return home


def _entry(name: str = "Stithy", role: str = "l2_project") -> AgentEntry:
    return AgentEntry(name=name, role=role, profile=name.lower())


def test_l2_identity_lands_in_the_shared_layer(soul_home):
    """写侧默认落共享层，不再需要「家」先存在。"""
    entry = _entry()

    assert ensure_l2_identity_soul(entry) is True

    path = shared_soul_path_for("Stithy")
    assert path.is_file(), path
    assert path.parent == shared_souls_root()
    assert "PLOBI_L2_IDENTITY" in path.read_text(encoding="utf-8")


def test_read_side_prefers_the_shared_copy_when_agent_id_is_set(soul_home, monkeypatch):
    """读侧：有 `PLOBI_AGENT_ID` ⇒ 读到共享层那一份（L2 的身份）。"""
    ensure_l2_identity_soul(_entry())

    # 顶层另放一份**不同**的内容：读侧若走错路就会被它糊弄过去。
    (soul_home / "SOUL.md").write_text("TOP-LEVEL-SOUL", encoding="utf-8")
    monkeypatch.setenv("PLOBI_AGENT_ID", "Stithy")

    from agent.prompt_builder import load_soul_md

    got = load_soul_md() or ""
    assert "PLOBI_L2_IDENTITY" in got, got[:120]
    assert "TOP-LEVEL-SOUL" not in got


def test_l1_path_is_untouched_without_agent_id(soul_home, monkeypatch):
    """读侧：没有 `PLOBI_AGENT_ID`（L1）⇒ 仍读顶层那一份，逐字不变。"""
    ensure_l2_identity_soul(_entry())
    (soul_home / "SOUL.md").write_text("TOP-LEVEL-SOUL", encoding="utf-8")
    monkeypatch.delenv("PLOBI_AGENT_ID", raising=False)

    from agent.prompt_builder import load_soul_md

    assert (load_soul_md() or "").strip() == "TOP-LEVEL-SOUL"


def test_identity_survives_a_home_that_is_not_there(soul_home, monkeypatch):
    """家不在 ⇒ 共享层那份照样写、照样读得到。

    这条正是 10-23 11:36 的同类：判据曾被「盘上有没有家」绑住，一次数据搬家
    把整条左栏打瘫。身份不能再犯——所以默认路径**不以家存在为前提**。
    """
    entry = _entry()
    home_dir = soul_home / "profiles" / "stithy"
    assert not home_dir.exists(), "前提：这一行的家不在盘上"

    assert ensure_l2_identity_soul(entry) is True
    assert shared_soul_path_for("Stithy").is_file()

    monkeypatch.setenv("PLOBI_AGENT_ID", "Stithy")
    from agent.prompt_builder import load_soul_md

    assert "PLOBI_L2_IDENTITY" in (load_soul_md() or "")


def test_writing_twice_is_a_noop(soul_home):
    """幂等：内容没变时第二次不写（既有契约，不许因为搬落点而丢掉）。"""
    entry = _entry()

    assert ensure_l2_identity_soul(entry) is True
    first = shared_soul_path_for("Stithy").read_text(encoding="utf-8")

    assert ensure_l2_identity_soul(entry) is False
    assert shared_soul_path_for("Stithy").read_text(encoding="utf-8") == first


def test_l1_row_is_never_written(soul_home):
    """L1 / default 不写（既有契约：那份是顶层模板的事，不是这一刀的）。"""
    assert ensure_l2_identity_soul(_entry(name="Plobi", role="l1_secretary")) is False
    assert not shared_soul_path_for("Plobi").exists()


def test_a_chinese_agent_name_still_gets_a_safe_filename(soul_home):
    """中文名 slug 成 hex digest——文件名必须始终是 `[A-Za-z0-9_-]+`。"""
    entry = _entry(name="电工电子学", role="l2_project")

    assert ensure_l2_identity_soul(entry) is True

    path = shared_soul_path_for("电工电子学")
    assert path.is_file(), path
    assert path.name == "proj-bbd45c368645.md" or path.name.startswith("proj-")


def test_boot_refresh_rewrites_old_fences_without_touching_the_persona(
    soul_home, tmp_path, monkeypatch
):
    """契约改版后，已经在盘上的分身不会自己更新（身份只在 spawn 时落盘）。

    用户 10-31 报「L2 第一反应也是自己干活」，改的是身份围栏；若只有新建的
    Agent 吃到新契约，24 个老分身照旧——所以启动要过一遍。判据：旧围栏整段换成
    新版，围栏外的人设一个字不动，第二遍不再写盘。
    """
    from plobi.agents.registry import (
        L2_SOUL_BEGIN,
        L2_SOUL_END,
        refresh_l2_identity_souls,
    )

    roster = tmp_path / "projects.yaml"
    roster.write_text(
        "version: 1\nagents:\n  Stithy:\n    role: l2_project\n    category: projects\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PLOBI_PROJECTS_CONFIG", str(roster))

    path = shared_soul_path_for("Stithy")
    path.parent.mkdir(parents=True, exist_ok=True)
    persona = "- 说话短，先给结论。"
    path.write_text(
        f"{persona}\n\n{L2_SOUL_BEGIN}\n## 旧版身份段\n"
        f"- 派工、日程入口、跨项目统筹归 L1。\n{L2_SOUL_END}\n",
        encoding="utf-8",
    )

    assert refresh_l2_identity_souls() == 1
    text = path.read_text(encoding="utf-8")
    assert persona in text
    assert "旧版身份段" not in text
    assert "统筹者" in text
    assert refresh_l2_identity_souls() == 0


def _roster(tmp_path, monkeypatch, body):
    roster = tmp_path / "projects.yaml"
    roster.write_text(body, encoding="utf-8")
    monkeypatch.setenv("PLOBI_PROJECTS_CONFIG", str(roster))
    return roster


def test_a_name_that_slugifies_differently_still_reads_its_own_soul(
    soul_home, tmp_path, monkeypatch
):
    """10-31 现场：「电子工程训练（乙）」答「我是你本机的总秘书」。

    读侧拿的是**记忆那颗 slug**（`resolve_agent_slug` 把全角括号换成 '-'），
    再用它去算 `souls/<名字>.md` 的文件名——名字一变形就算不出自己那份，
    于是静默回落到顶层那份 **L1 秘书 SOUL**，分身当场自称总秘书。
    `45b9081d` 修的是 ASCII 名（slug 与名字只差大小写，这台盘不敏感）；
    名字里带全角括号/罗马数字的那五门课还在踩这一脚。
    """
    name = "电子工程训练（乙）"
    _roster(
        tmp_path,
        monkeypatch,
        f"version: 1\nagents:\n  {name}:\n    role: l2_project\n"
        f"    category: courses\n    profile: l2-course-ee\n",
    )

    entry = AgentEntry(
        name=name, role="l2_project", profile="l2-course-ee", category="courses"
    )
    assert ensure_l2_identity_soul(entry) is True
    (soul_home / "SOUL.md").write_text("L1-SECRETARY-SOUL", encoding="utf-8")

    from tools.memory_tool import resolve_agent_slug

    assert resolve_agent_slug(name) != name, "这颗 slug 与名字不同——正是踩雷的形状"

    # 桌面正路：进程环境里没有 PLOBI_AGENT_ID，身份从会话那一行反推（反推回来是名字）
    monkeypatch.delenv("PLOBI_AGENT_ID", raising=False)
    monkeypatch.setattr(
        "tools.memory_tool._agent_slug_from_current_session", lambda: name
    )

    from agent.prompt_builder import load_soul_md

    got = load_soul_md() or ""
    assert "PLOBI_L2_IDENTITY" in got, got[:160]
    assert "L1-SECRETARY-SOUL" not in got


def test_an_unresolvable_identity_never_borrows_the_l1_secretary_soul(
    soul_home, tmp_path, monkeypatch
):
    """认不出是哪一行 ⇒ 宁可不给身份，也不许把 L1 的秘书合同印到分身身上。"""
    _roster(tmp_path, monkeypatch, "version: 1\nagents: {}\n")
    (soul_home / "SOUL.md").write_text("L1-SECRETARY-SOUL", encoding="utf-8")

    monkeypatch.delenv("PLOBI_AGENT_ID", raising=False)
    monkeypatch.setattr(
        "tools.memory_tool._agent_slug_from_current_session", lambda: "没有这一行"
    )

    from agent.prompt_builder import load_soul_md

    assert load_soul_md() is None


def test_identity_name_for_key_maps_every_spelling_of_the_same_row(
    soul_home, tmp_path, monkeypatch
):
    """身份的写法不止一种（名册 name／记忆 slug／profile 家名／身份文件名），都要翻回同一行。"""
    name = "电子工程训练（乙）"
    _roster(
        tmp_path,
        monkeypatch,
        f"version: 1\nagents:\n  {name}:\n    role: l2_project\n"
        f"    category: courses\n    profile: l2-course-ee\n",
    )

    from plobi.agents.registry import identity_name_for_key, shared_soul_path_for
    from tools.memory_tool import resolve_agent_slug

    for key in (
        name,
        name.lower(),
        resolve_agent_slug(name),
        "l2-course-ee",
        shared_soul_path_for(name).stem,
    ):
        assert identity_name_for_key(key) == name, key

    assert identity_name_for_key("") == ""
    assert identity_name_for_key("没有这一行") == ""
