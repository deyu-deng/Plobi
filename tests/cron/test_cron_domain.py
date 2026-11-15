"""结构化来源自带域：Cron 作业记录上的 ``domain``（裁定 219）。

裁定 218 把「谁负责什么」搬进了名册行，但那一跳的 ``domain`` 仍要靠模型在
``say`` / ``ask`` 里自己填——「这件事属于哪一摊」还是软的。Cron 不一样：作业记录
上的收件人和域都是**人写作业时顺手写下的结构事实**，到点照着执行就行。这条闸门
就是在花钱之前，用名册的域表核一遍这条作业有没有配错。

四条判据各咬一头：

① 记录上写了域 ⇒ 收件人那一行必须声明负责它，否则这一轮**根本不起**
   （脚本不跑、AIAgent 不构造、一个模型都不打），错因里说得出真正的负责人是谁；
② 域没人认领（多半是写错字）⇒ 同样不起，理由里明确说「没有」；
③ 没写域 ⇒ 与裁定 219 之前逐字相同（`**Domain:**` 那一行也不出现）；
④ ``no_agent`` 的纯脚本作业不经过 Agent ⇒ 域闸门不许把它们一起拦掉。

另外两条钉住「记录层只存原文」：写进去的值不被改写、清空就真的清掉这一格。
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from cron.scheduler import run_job
from plobi.agents.registry import AgentEntry, AgentRegistry

RECIPIENT = "agenda"


def _roster(tmp_path: Path, *entries: AgentEntry) -> str:
    """一份真名册（产品自己的 writer），让产品的解析器指过去。"""
    path = tmp_path / "projects.yaml"
    AgentRegistry(path=path, agents={entry.name: entry for entry in entries}).save()
    return str(path)


def _agenda_row(tmp_path: Path, **fields) -> str:
    return _roster(
        tmp_path,
        AgentEntry(
            name=RECIPIENT,
            role="l2_project",
            display_name="日程秘书",
            description="日程采集 → SQLite → 看板 → 钉钉 闭环",
            **fields,
        ),
    )


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """日程秘书那一行**声明了** ``schedule`` 这个域（category 仍是默认那格）。"""
    monkeypatch.setenv(
        "PLOBI_PROJECTS_CONFIG", _agenda_row(tmp_path, domains=("schedule",))
    )
    return tmp_path


def _job(**overrides):
    job = {
        "id": "dom-test",
        "name": "plobi-evening-plan",
        "prompt": "今晚这一轮由你自己把明天排出来",
        "recipient": RECIPIENT,
        "domain": None,
        "model": None,
        "provider": None,
        "provider_snapshot": None,
        "model_snapshot": None,
        "base_url": None,
        "no_agent": False,
        "script": "plobi_evening_plan.py",
    }
    job.update(overrides)
    return job


def _fire(job, home, *, docs: list | None = None):
    """跑一次 ``run_job``：AIAgent / 运行时解析 / 脚本执行都换成可观测的桩。"""
    with (
        patch("cron.scheduler._plobi_home", home),
        patch("cron.scheduler._resolve_origin", return_value=None),
        patch("plobi_cli.env_loader.load_plobi_dotenv"),
        patch("plobi_cli.env_loader.reset_secret_source_cache"),
        patch("plobi_state.SessionDB", return_value=MagicMock()),
        patch(
            "plobi_cli.runtime_provider.resolve_runtime_provider",
            return_value={
                "api_key": "test-key",
                "base_url": "https://example.invalid/v1",
                "provider": "bezel",
                "api_mode": "chat_completions",
            },
        ),
        patch(
            "cron.scheduler._run_job_script",
            return_value=(True, '{"wakeAgent": true}'),
        ) as script_runner,
        patch("run_agent.AIAgent") as agent_cls,
    ):
        agent = MagicMock()
        agent.run_conversation.return_value = {"final_response": "排好了"}
        agent_cls.return_value = agent
        success, output, _final, error = run_job(job)
    if docs is not None:
        docs.append(output)
    return success, error, agent_cls, script_runner


# ---------------------------------------------------------------- 判据 ① / ②


def test_a_recipient_that_does_not_own_the_domain_never_starts(tmp_path, monkeypatch):
    """作业指名 agenda，却把域写成 schedule——而那一行没声明负责它 ⇒ 不起。"""
    monkeypatch.setenv("PLOBI_PROJECTS_CONFIG", _agenda_row(tmp_path))  # 只声明了 category

    success, error, agent_cls, script_runner = _fire(_job(domain="schedule"), tmp_path)

    assert success is False
    assert "schedule" in error
    assert "没有任何一行声明负责" in error, error
    agent_cls.assert_not_called()
    script_runner.assert_not_called()  # 连 wake-gate 脚本都不跑

def test_the_refusal_names_the_real_owner(tmp_path, monkeypatch):
    """错配不止说「不行」：要把真正负责这一摊的行列出来，人才改得动。"""
    monkeypatch.setenv(
        "PLOBI_PROJECTS_CONFIG",
        _roster(
            tmp_path,
            AgentEntry(name="agenda", role="l2_project", domains=("schedule",)),
            AgentEntry(name="butler", role="l2_butler"),
        ),
    )

    success, error, agent_cls, _script = _fire(_job(recipient="butler", domain="schedule"), tmp_path)

    assert success is False
    assert "agenda" in error, error
    agent_cls.assert_not_called()


def test_a_domain_nobody_declares_never_starts(home):
    """写错字（``sched``）⇒ 没人认领这个域，一样不起，理由里说得出「没有」。"""
    success, error, agent_cls, _script = _fire(_job(domain="sched"), home)

    assert success is False
    assert "没有" in error and "sched" in error, error
    agent_cls.assert_not_called()


def test_an_unreadable_roster_does_not_start_an_unverified_round(home):
    """名册读不出来 = 没法核对 ⇒ 不起。这一侧与收件人闸门同一条口径（裁定 177）。"""
    success, error, agent_cls, _script = _fire(_job(domain="schedule"), home)

    assert success is True, "正常情况先确认这条作业本来是能跑的"
    with patch("plobi.agents.registry.load_registry", side_effect=RuntimeError("名册坏了")):
        success, error, agent_cls, _script = _fire(_job(domain="schedule"), home)
    assert success is False
    assert "没法核对" in error, error


# ------------------------------------------------------------------- 判据 ③


def test_a_declared_domain_lets_the_round_start(home):
    """那一行声明了它 ⇒ 这一轮照跑，留痕里写着这一轮属于哪一摊。"""
    docs: list[str] = []
    success, error, agent_cls, _script = _fire(
        _job(domain="schedule"), home, docs=docs
    )

    assert success is True, error
    agent_cls.assert_called_once()
    assert "**Domain:** schedule" in docs[0]


def test_a_case_variant_on_the_record_still_matches(home):
    """记录上写 ``Schedule`` 也认：两边走同一套归一，不是一个域。"""
    success, error, _agent, _script = _fire(_job(domain="Schedule"), home)

    assert success is True, error


def test_the_category_alone_counts_as_a_domain(home, tmp_path, monkeypatch):
    """存量行零迁移：``category`` 本身就是一个域，一行都没改也能当负责人。"""
    monkeypatch.setenv(
        "PLOBI_PROJECTS_CONFIG",
        _roster(tmp_path, AgentEntry(name=RECIPIENT, role="l2_project", category="projects")),
    )

    success, error, agent_cls, _script = _fire(_job(domain="projects"), tmp_path)

    assert success is True, error
    agent_cls.assert_called_once()


def test_a_job_without_a_domain_is_unchanged(home):
    """没写域 = 这一层完全不介入：照旧跑，留痕里不许多出一行。"""
    docs: list[str] = []
    success, error, agent_cls, _script = _fire(_job(), home, docs=docs)

    assert success is True, error
    agent_cls.assert_called_once()
    assert "**Domain:**" not in docs[0], "没声明过的格不许在留痕里凭空出现"


# ------------------------------------------------------ 闸门不许扩大到脚本作业


def test_a_no_agent_job_is_not_gated_by_the_domain(home):
    """采集 / 晨报那几条不经过 Agent——域闸门不许它们一起停摆。"""
    job = _job(recipient=None, no_agent=True, script="plobi_collect_sweep.py", domain="ghost")
    with (
        patch("cron.scheduler._plobi_home", home),
        patch("cron.scheduler._run_job_script", return_value=(True, "采集完 3 条")) as script_runner,
        patch("run_agent.AIAgent") as agent_cls,
    ):
        success, _output, final, error = run_job(job)

    assert success is True, error
    assert final == "采集完 3 条"
    script_runner.assert_called_once()
    agent_cls.assert_not_called()


# --------------------------------------------------------- 记录层只存原文的那一格


def test_the_record_keeps_the_domain_and_clears_on_blank(tmp_path, monkeypatch):
    """写进去照原文存、清空就真的清掉——别留一个空串被读成「声明了空域」。"""
    from cron.jobs import create_job, get_job, update_job, use_cron_store

    monkeypatch.setenv("PLOBI_PROJECTS_CONFIG", _agenda_row(tmp_path, domains=("schedule",)))
    with use_cron_store(tmp_path):
        job = create_job(
            prompt="x", schedule="every 5m", name="早报", recipient=RECIPIENT, domain="Schedule"
        )
        assert get_job(job["id"])["domain"] == "Schedule"

        update_job(job["id"], {"domain": "   "})
        assert get_job(job["id"]).get("domain") is None

        update_job(job["id"], {"domain": "schedule"})
        assert get_job(job["id"])["domain"] == "schedule"


def test_a_job_created_without_a_domain_has_no_such_key(tmp_path, monkeypatch):
    """老作业的持久形状不该被这条改动改变：没写就没有这一格。"""
    from cron.jobs import create_job, get_job, use_cron_store

    monkeypatch.setenv("PLOBI_PROJECTS_CONFIG", _agenda_row(tmp_path))
    with use_cron_store(tmp_path):
        job = create_job(prompt="x", schedule="every 5m", name="早报", recipient=RECIPIENT)
        assert "domain" not in get_job(job["id"])
