"""Course rows in the left rail must be clickable — 裁定 143②/145①/146②(b).

The 2026-10-21 symptom: every ``courses`` row rendered greyed out and
unclickable. Root cause was a **role mislabel in the data**, not a policy
decision: the rows carried 裁定 143②'s "identity granted" shape (``category:
courses`` + a name that derives an ASCII slug) but their ``role`` still pointed
at「还没有拿到身份」那一档。``AgentEntry.profile_name`` 对那种行返回 ""，于是
``agent_navigability`` 第一道否决开火，整列灰掉。

**2026-11-15（用户裁定）**：那一档（第三个 role 值）已经物理删除。
**2026-11-16（i18n 定名刀）**：slug 派生层整层删除——「有没有身份」的判据变成
:func:`agent_id_for` 拼出的门牌 ``<role>-<name>`` 是否为空串。名字必须是 ASCII
（翻译在录入侧做，中文留在 ``display_name``），数据里写着的 ``profile`` 不算数。

This file pins the contract that closes it:

1. A course row WHOSE NAME yields a doorplate is navigable — the same
   clickability any project 分身 gets (「五种 Agent 完全平等」).
2. A course row WHOSE NAME yields no doorplate (中文名直接当 ``name``) stays
   non-navigable — that row genuinely has no runtime to bind, and pretending
   otherwise is what produced the 2026-10-13 restart-loop bug.
3. Askability follows the same identity: a row with a slug is askable like any
   other L2, so 派活/提问 paths see courses and projects identically.

Isolation: every test runs against a temp ``PLOBI_HOME`` + temp roster file.
Nothing here may read or write the real ``~/.plobi`` — the navigability probe
checks the live home, so the env must be pinned or the test silently reads the
real machine's state.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from plobi.agents.registry import (
    AgentRegistry,
    agent_askability,
    agent_navigability,
)
from plobi.routing.models import PRINCIPAL


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """Temp PLOBI_HOME + temp roster file; nothing leaks to the real home.

    这里**直接钉住 ``profile_exists``**（同 ``test_agent_edit.py`` 的手法），
    而不是靠 ``PLOBI_HOME`` 让 ``plobi_cli.agent_identity`` 自己算出来：那套解析要看
    `native home`、`active_profile` 这些进程级状态，前一个测试文件如果碰过它们
    （实测：与 `test_agent_taxonomy.py` 同批跑时），本文件的「家落地了没有」读数
    就会飘——单跑绿、组合红，红的那条与用例本身无关。
    """
    plobi_home = tmp_path / "plobi_home"
    (plobi_home / "plobi").mkdir(parents=True)
    (plobi_home / "profiles").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("PLOBI_HOME", str(plobi_home))
    monkeypatch.setenv("PLOBI_PROJECTS_CONFIG", str(plobi_home / "plobi" / "projects.yaml"))
    monkeypatch.delenv("PLOBI_STATE_HOME", raising=False)

    import plobi_cli.agent_identity as _profiles

    monkeypatch.setattr(
        _profiles,
        "profile_exists",
        lambda name: (_profiles.get_profile_dir(str(name)) if str(name) != "default" else plobi_home).is_dir()
        or (plobi_home / "profiles" / str(name)).is_dir(),
        raising=False,
    )
    return plobi_home


def _registry_file(tmp_path) -> Path:
    return tmp_path / "plobi_home" / "plobi" / "projects.yaml"


def _landing_home(tmp_path, profile: str) -> Path:
    home_dir = tmp_path / "plobi_home" / "profiles" / profile
    home_dir.mkdir(parents=True, exist_ok=True)
    return home_dir


def _load(tmp_path, agents: dict) -> AgentRegistry:
    path = _registry_file(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(
            {"version": 1, "agents": agents}, sort_keys=False, allow_unicode=True
        ),
        encoding="utf-8",
    )
    return AgentRegistry.load(path)


def test_identified_course_row_is_navigable_and_askable(tmp_path, home):
    """裁定 143② shape: ASCII 门牌 + landed home ⇒ 同列同点。"""
    _landing_home(tmp_path, "principal-ee")
    reg = _load(
        tmp_path,
        {
            "ee": {
                "display_name": "电工电子学",
                "role": PRINCIPAL,
                "category": "courses",
            }
        },
    )
    entry = reg.get("ee")

    navigable, reason = agent_navigability(entry)
    assert navigable is True, reason
    assert agent_askability(entry)[0] is True
    # The DTO field the rail consumes must carry the doorplate, not "".
    assert entry.profile_name == "principal-ee"


def test_identified_course_row_is_clickable_without_a_home(tmp_path, home):
    """裁定 192（10-23）：有身份的课程行**不需要盘上有一个家**才可点。

    这条原来钉的是反方向（"家没落地就先把行灰掉，否则点了 spawn 失败"）。那个前提随
    单后端（裁 168）与乙案（裁 178）一起没了：点击不再 spawn 一个带 `--profile` 的进程，
    而把家当"能不能点"的证据，会让一次数据搬家把整条左栏打瘫——10-23 11:36 就是这么红的。
    防 2026-10-13 那个重启循环的牙在隔壁那条：拼不出门牌的行仍然不可点。
    """
    reg = _load(
        tmp_path,
        {
            "ee": {
                "display_name": "电工电子学",
                "role": PRINCIPAL,
                "category": "courses",
            }
        },
    )
    entry = reg.get("ee")

    assert not (home / "profiles" / "principal-ee").exists()  # 前提：盘上没有这个家
    navigable, reason = agent_navigability(entry)
    assert navigable is True
    assert reason == ""


def test_course_row_without_identity_stays_non_navigable(tmp_path, home):
    """中文名直接当 ``name`` ⇒ 拼不出合法门牌 ⇒ 无路由。

    This is the shape that MUST stay grey — the 2026-10-13 restart-loop bug.
    """
    reg = _load(
        tmp_path,
        {"一门没有身份的课": {"role": PRINCIPAL, "category": "courses"}},
    )
    entry = reg.get("一门没有身份的课")

    navigable, reason = agent_navigability(entry)
    assert navigable is False
    assert entry.profile_name == ""
    assert agent_askability(entry)[0] is False


def test_row_whose_name_derives_no_slug_is_not_clickable(tmp_path, home):
    """数据里那格 ``profile`` 不再算数——身份只认 ``<role>-<name>`` 拼出来的门牌。

    原来是钉 2026-10-21 那个「role 说不算、数据说算」的错标形状。第三值删掉之后
    那个形状不可能再存在了，牙改成钉新判据：即便有人在 yaml 里硬塞一个 ``profile``，
    拼不出门牌的行照样灰着、``profile_name`` 照样是空串。
    """
    _landing_home(tmp_path, "l2-course-ee")
    reg = _load(
        tmp_path,
        {
            "线性代数": {
                "role": PRINCIPAL,
                "category": "courses",
                "profile": "l2-course-ee",
            }
        },
    )
    entry = reg.get("线性代数")

    assert entry.profile_name == ""
    assert agent_navigability(entry)[0] is False
