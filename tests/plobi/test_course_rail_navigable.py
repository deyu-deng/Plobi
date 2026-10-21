"""Course rows in the left rail must be clickable — 裁定 143②/145①/146②(b).

The 2026-10-21 symptom: every ``courses`` row rendered greyed out and
unclickable. Root cause was a **role mislabel in the data**, not a policy
decision: the rows carried 裁定 143②'s "identity granted" shape (``category:
courses`` + explicit ASCII ``profile: l2-course-*`` + a landed profile home)
but their ``role`` still said ``l2_course`` — the role that means「还没有拿到
身份」. ``AgentEntry.profile_name`` returns "" for that role (by design), so
``agent_navigability``'s first veto fired and the rail greys them out.

This file pins the contract that closes it:

1. A course row WITH identity (explicit profile + landed home) is navigable —
   the same clickability any project 分身 gets (「五种 Agent 完全平等」).
2. A course row WITHOUT identity (``l2_course``, no profile) stays
   non-navigable — that shape genuinely has no runtime to bind, and pretending
   otherwise is what produced the 2026-10-13 restart-loop bug.
3. Askability follows the same identity: an identified course row is askable
   like any other L2, so 派活/提问 paths see courses and projects identically.

The data repair itself (flipping the mislabeled rows' role) is a one-time
operation recorded in the agent register; what must never regress is the
reading contract above.

Isolation: every test runs against a temp ``PLOBI_HOME`` + temp roster file.
Nothing here may read or write the real ``~/.plobi`` — the navigability probe
checks ``profiles/<name>`` under the live home, so the env must be pinned or
the test silently reads the real machine's homes.
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
from plobi.routing.models import L2_COURSE, L2_PROJECT


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """Temp PLOBI_HOME + temp roster file; nothing leaks to the real home.

    这里**直接钉住 ``profile_exists``**（同 ``test_agent_edit.py`` 的手法），
    而不是靠 ``PLOBI_HOME`` 让 ``plobi_cli.profiles`` 自己算出来：那套解析要看
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

    import plobi_cli.profiles as _profiles

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
    """裁定 143② shape: explicit profile + landed home ⇒ 同列同点."""
    _landing_home(tmp_path, "l2-course-ee")
    reg = _load(
        tmp_path,
        {
            "电工电子学": {
                "role": L2_PROJECT,
                "category": "courses",
                "profile": "l2-course-ee",
            }
        },
    )
    entry = reg.get("电工电子学")

    navigable, reason = agent_navigability(entry)
    assert navigable is True, reason
    assert agent_askability(entry)[0] is True
    # The DTO field the rail consumes must carry the profile, not "".
    assert entry.profile_name == "l2-course-ee"


def test_identified_course_row_is_refused_while_its_home_is_missing(tmp_path, home):
    """Same row, home not landed yet: still refused — clicking would spawn-fail.
    The fix for that is landing the home (spawn), not greying the row forever."""
    reg = _load(
        tmp_path,
        {
            "电工电子学": {
                "role": L2_PROJECT,
                "category": "courses",
                "profile": "l2-course-ee",
            }
        },
    )
    entry = reg.get("电工电子学")

    navigable, reason = agent_navigability(entry)
    assert navigable is False
    assert "落地" in reason or "家" in reason


def test_course_row_without_identity_stays_non_navigable(tmp_path, home):
    """``l2_course`` with no profile = 数据记录 (裁定 40): no runtime to bind.
    This is the shape that MUST stay grey — the 2026-10-13 restart-loop bug."""
    reg = _load(
        tmp_path,
        {"一门没有身份的课": {"role": L2_COURSE, "category": "courses"}},
    )
    entry = reg.get("一门没有身份的课")

    navigable, reason = agent_navigability(entry)
    assert navigable is False
    assert entry.profile_name == ""
    assert agent_askability(entry)[0] is False


def test_mislabeled_course_row_is_the_bug_shape(tmp_path, home):
    """The 2026-10-21 bug, pinned as a failing shape: identity in the data
    (explicit profile + landed home) but the role still says ``l2_course``.
    ``profile_name`` must NOT hand out a profile for that role — that is what
    makes the mislabel visible instead of silently clickable."""
    _landing_home(tmp_path, "l2-course-ee")
    reg = _load(
        tmp_path,
        {
            "电工电子学": {
                "role": L2_COURSE,
                "category": "courses",
                "profile": "l2-course-ee",
            }
        },
    )
    entry = reg.get("电工电子学")

    assert entry.profile_name == ""
    assert agent_navigability(entry)[0] is False
