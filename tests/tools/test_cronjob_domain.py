"""入口那一半：嘴建作业时就得把域对上名册（裁定 219）。

闸门（``cron/scheduler.py``）只在到点时拦，而作业几乎都是 Agent 用 ``cronjob``
这张嘴建出来的。入口不认这个域，就会建出一条「生下来就是死的」作业——正是用户
抱怨的那种「ok 但什么都没发生」。所以这一侧与收件人（裁定 177）同形：写错当场
拒绝，并把真正的负责人列出来。

三条判据：① 域对得上就落进记录；② 域对不上当场拒绝，错因里点出负责人；
③ 不写域 = 与以前逐字相同（不校验）；④ 更新时换域同样要核，清空合法。
"""

from __future__ import annotations

import json

import pytest

from tools.cronjob_tools import cronjob

OWNER = "cron-domain-agenda"
OTHER = "cron-domain-butler"


@pytest.fixture(autouse=True)
def _roster(tmp_path, monkeypatch):
    """一份真名册：``agenda`` 声明了 ``schedule``，``butler`` 只有 category。"""
    from plobi.agents.registry import AgentEntry, AgentRegistry

    path = tmp_path / "domain-roster.yaml"
    rows = {
        OWNER: AgentEntry(
            name=OWNER, role="principal", display_name=OWNER, domains=("schedule",)
        ),
        OTHER: AgentEntry(name=OTHER, role="l2_butler", display_name=OTHER),
    }
    AgentRegistry(path=path, agents=rows).save()
    monkeypatch.setenv("PLOBI_PROJECTS_CONFIG", str(path))
    monkeypatch.delenv("PLOBI_AGENT_ID", raising=False)
    yield


def _run(**kwargs):
    return json.loads(cronjob(**kwargs))


def _create(**kwargs):
    args = {
        "action": "create",
        "prompt": "把今天还开口的口子列一遍",
        "schedule": "every 1d",
    }
    args.update(kwargs)
    return _run(**args)


def test_a_matching_domain_lands_on_the_job_record():
    out = _create(recipient=OWNER, domain="schedule")

    assert out["success"] is True, out.get("error")
    assert out["job"]["domain"] == "schedule"


def test_a_case_variant_is_normalized_to_the_roster_spelling():
    """``Schedule`` 与 ``schedule`` 是同一个域——两边走同一套归一。"""
    out = _create(recipient=OWNER, domain="Schedule")

    assert out["success"] is True, out.get("error")
    assert out["job"]["domain"] == "schedule"


def _ids() -> set:
    """作业库里现存的所有 id。测试档共用一份 cron 库里可能有别的档建的行，
    所以判「有没有多建一条」只能比 id 集合，不能比条数。"""
    return {
        j["job_id"] for j in _run(action="list", include_disabled=True).get("jobs", [])
    }


def test_a_foreign_domain_is_refused_and_names_the_owner():
    """收件人那一行没声明这个域 ⇒ 当场拒绝，并把真正的负责人列出来。"""
    before = _ids()

    out = _create(recipient=OTHER, domain="schedule")

    assert out["success"] is False
    assert "没有声明负责" in out["error"], out["error"]
    assert OWNER in out["error"], "要说出该找谁"
    assert _ids() == before, "拒绝就是没建，别留一条死作业"


def test_a_domain_nobody_owns_is_refused():
    out = _create(recipient=OWNER, domain="no-such-bucket")

    assert out["success"] is False
    assert "no-such-bucket" in out["error"]
    assert "没有" in out["error"]


def test_without_a_domain_nothing_is_checked():
    """不写域 = 这一层完全不介入（裁定 219 之前的行为逐字不变）。"""
    out = _create(recipient=OTHER)

    assert out["success"] is True, out.get("error")
    assert out.get("job", {}).get("domain") is None


def test_a_no_agent_job_does_not_need_a_domain():
    out = _create(no_agent=True, script="plobi_collect_sweep.py", recipient=None)

    assert out["success"] is True, out.get("error")


def test_updating_a_job_to_a_foreign_domain_is_refused():
    created = _create(recipient=OWNER, domain="schedule")
    job_id = created["job_id"]

    out = _run(action="update", job_id=job_id, recipient=OTHER, domain="schedule")

    assert out["success"] is False
    assert OWNER in out["error"], out["error"]
    rows = {j["job_id"]: j for j in _run(action="list", include_disabled=True)["jobs"]}
    assert rows[job_id]["recipient"] == OWNER, "被拒绝的更新不许落地一半"


def test_a_domain_can_be_cleared():
    """清空合法：回到「不校验」，而不是留一个空串当「声明了空域」。"""
    created = _create(recipient=OWNER, domain="schedule")
    job_id = created["job_id"]

    out = _run(action="update", job_id=job_id, domain="")

    assert out["success"] is True, out.get("error")
    assert out["job"].get("domain") is None
