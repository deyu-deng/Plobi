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
