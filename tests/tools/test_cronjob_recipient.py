"""由嘴建作业也得有主人：``recipient`` 这一格从 cron 工具面写进作业记录。

裁定 177 的入口那一半。闸门（``cron/scheduler.py``）只拦「到点这一轮归谁跑」，
而作业绝大多数是 Agent 用 ``cronjob`` 这张嘴建出来的——入口不认这个名字，
建出来的每一条都是生下来就死的作业，正是用户抱怨的「ok 但什么都没发生」。

名册用 ``PLOBI_PROJECTS_CONFIG`` 指向一份真档（两行活的 + 一行归档），
作业库走全局 conftest 那份每测试独立的 ``PLOBI_HOME`` 临时目录。
"""

import json
from dataclasses import replace

import pytest

from tools.cronjob_tools import cronjob

OWNER = "cron-owner-agent"
OTHER = "cron-other-agent"
GONE = "cron-archived-agent"


@pytest.fixture(autouse=True)
def _roster(tmp_path, monkeypatch):
    """一份真名册（产品自己的写入器），收件人这一侧解析的就是它。"""
    from plobi.agents.registry import AgentEntry, AgentRegistry

    path = tmp_path / "recipient-roster.yaml"
    rows = {
        name: AgentEntry(name=name, role="l2_test", profile=name, display_name=name)
        for name in (OWNER, OTHER)
    }
    rows[GONE] = replace(
        AgentEntry(name=GONE, role="l2_test", profile=GONE, display_name=GONE),
        archived=True,
        archived_at="2026-10-19",
    )
    AgentRegistry(path=path, agents=rows).save()
    monkeypatch.setenv("PLOBI_PROJECTS_CONFIG", str(path))
    # 默认这句是 L1 在说话：名册里没有 L1 那一行。
    monkeypatch.delenv("PLOBI_AGENT_ID", raising=False)
    yield


def _run(**kwargs):
    return json.loads(cronjob(**kwargs))


def _create(**kwargs):
    args = {"action": "create", "prompt": "把今天还开口的口子列一遍", "schedule": "every 1d"}
    args.update(kwargs)
    return _run(**args)


class TestCreate:
    def test_named_recipient_lands_on_the_job_record(self):
        out = _create(recipient=OTHER)
        assert out["success"] is True
        assert out["job"]["recipient"] == OTHER

    def test_unknown_recipient_is_refused_and_lists_the_live_names(self):
        before = _run(action="list", include_disabled=True)["count"]
        out = _create(recipient="no-such-agent")
        assert out["success"] is False
        assert "不在名册里" in out["error"]
        assert OWNER in out["error"] and OTHER in out["error"]
        # 拒了就不许留半条作业在库里
        assert _run(action="list", include_disabled=True)["count"] == before

    def test_archived_recipient_is_refused(self):
        out = _create(recipient=GONE)
        assert out["success"] is False
        assert "已归档" in out["error"]

    def test_name_is_stored_canonical_not_as_typed(self):
        out = _create(recipient="Cron-Owner-Agent")
        assert out["success"] is True
        assert out["job"]["recipient"] == OWNER

    def test_no_recipient_defaults_to_the_agent_that_is_speaking(self, monkeypatch):
        monkeypatch.setenv("PLOBI_AGENT_ID", OTHER)
        out = _create(name="由 L2 自己建的作业")
        assert out["success"] is True
        assert out["job"]["recipient"] == OTHER
        assert "没有收件人" not in out["message"]

    def test_unattributable_creation_still_works_but_says_it_wont_fire(self):
        out = _create()
        assert out["success"] is True
        assert out["job"]["recipient"] is None
        assert "没有收件人" in out["message"] and "裁定 177" in out["message"]

    def test_script_jobs_need_no_recipient_and_get_no_warning(self):
        out = _create(no_agent=True, script="watchdog.sh")
        assert out["success"] is True
        assert "没有收件人" not in out["message"]


class TestUpdate:
    def test_recipient_can_be_moved_to_another_agent(self):
        created = _create(recipient=OWNER)
        out = _run(action="update", job_id=created["job_id"], recipient=OTHER)
        assert out["success"] is True
        assert out["job"]["recipient"] == OTHER

    def test_unknown_new_recipient_is_refused(self):
        created = _create(recipient=OWNER)
        out = _run(action="update", job_id=created["job_id"], recipient="no-such-agent")
        assert out["success"] is False
        assert "不在名册里" in out["error"]
        listed = {j["job_id"]: j for j in _run(action="list")["jobs"]}
        assert listed[created["job_id"]]["recipient"] == OWNER

    def test_clearing_the_recipient_needs_a_script_job(self):
        created = _create(recipient=OWNER)
        job_id = created["job_id"]
        refused = _run(action="update", job_id=job_id, recipient="")
        assert refused["success"] is False
        assert "清空" in refused["error"]
        cleared = _run(action="update", job_id=job_id, recipient="", no_agent=True, script="wd.sh")
        assert cleared["success"] is True
        assert cleared["job"]["recipient"] is None
        assert cleared["job"]["no_agent"] is True


class TestListingAndWiring:
    def test_list_shows_who_owns_each_job(self):
        _create(recipient=OTHER, name="有主人的")
        _create(name="没主人的")
        jobs = {j["name"]: j for j in _run(action="list")["jobs"]}
        assert jobs["有主人的"]["recipient"] == OTHER
        assert jobs["没主人的"]["recipient"] is None

    def test_handler_maps_the_recipient_argument(self):
        """注册面那条 lambda 忘了接 args 里的 recipient，模型写了也没用——这条钉住它。"""
        from tools.cronjob_tools import CRONJOB_SCHEMA, registry

        assert "recipient" in CRONJOB_SCHEMA["parameters"]["properties"]
        entry = registry.get_entry("cronjob")
        out = json.loads(
            entry.handler(
                {
                    "action": "create",
                    "prompt": "每天核一遍没关的口子",
                    "schedule": "every 1d",
                    "recipient": OWNER,
                }
            )
        )
        assert out["success"] is True
        assert out["job"]["recipient"] == OWNER
