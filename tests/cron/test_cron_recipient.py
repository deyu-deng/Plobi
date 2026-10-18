"""到点那一轮归谁跑：cron 记录上的收件人（裁定 177）。

三条判据各咬一头：
① 记录里没写收件人 ⇒ 那一轮**根本不起**——脚本不跑、AIAgent 不构造、一个模型都不打，
   绝不退回到「正好起着后端的那个家」；
② 写了但名册里查无此人 ⇒ 同样不起，理由里说得出那个名字；
③ 收件人是名册那一行 ⇒ 那一轮**按那一行**跑：会话号里带名字、系统提示词里署名，
   那一行自己声明的 provider/model 生效（裁 172：覆盖的单位是那一行，不是 role），
   没声明就按本机默认——**不折进角色路由**。

``no_agent`` 那几条（采集 / 晨报 / 额度提醒）不经过 Agent，因此不需要收件人：
最后一条测试专门咬住「闸门没被扩大」。
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import dataclasses

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from cron.scheduler import run_job
from plobi.agents.registry import AgentEntry, AgentRegistry

RECIPIENT = "agenda"


def _roster(tmp_path: Path, *entries: AgentEntry) -> str:
    """写一份真名册（产品自己的 writer），再用产品自己的解析器指过去。"""
    path = tmp_path / "projects.yaml"
    AgentRegistry(path=path, agents={entry.name: entry for entry in entries}).save()
    return str(path)


def _agenda_row(tmp_path: Path, **fields) -> str:
    return _roster(
        tmp_path,
        AgentEntry(
            name=RECIPIENT,
            role="l2_agenda",
            profile=RECIPIENT,
            display_name="日程秘书",
            description="日程采集 → SQLite → 看板 → 钉钉 闭环",
            **fields,
        ),
    )


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """一台只有日程秘书那一行的机器：名册里那一行**不带**模型覆盖。"""
    monkeypatch.setenv("PLOBI_PROJECTS_CONFIG", _agenda_row(tmp_path))
    return tmp_path


def _job(**overrides):
    job = {
        "id": "rcp-test",
        "name": "plobi-evening-plan",
        "prompt": "今晚这一轮由你自己把明天排出来",
        "recipient": RECIPIENT,
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
    """跑一次 ``run_job``：AIAgent / 运行时解析 / 脚本执行都换成可观测的桩。

    ``docs`` 只给需要读那份输出文档（留痕落点）的测试用——其余测试照旧五元组。
    """
    with patch("cron.scheduler._plobi_home", home), \
         patch("cron.scheduler._resolve_origin", return_value=None), \
         patch("plobi_cli.env_loader.load_plobi_dotenv"), \
         patch("plobi_cli.env_loader.reset_secret_source_cache"), \
         patch("plobi_state.SessionDB", return_value=MagicMock()), \
         patch(
             "plobi_cli.runtime_provider.resolve_runtime_provider",
             return_value={
                 "api_key": "test-key",
                 "base_url": "https://example.invalid/v1",
                 "provider": "aigw",
                 "api_mode": "chat_completions",
             },
         ) as resolve_provider, \
         patch(
             "cron.scheduler._run_job_script",
             return_value=(True, '{"wakeAgent": true}'),
         ) as script_runner, \
         patch("run_agent.AIAgent") as agent_cls:
        agent = MagicMock()
        agent.run_conversation.return_value = {"final_response": "排好了"}
        agent_cls.return_value = agent
        success, output, _final, error = run_job(job)
    if docs is not None:
        docs.append(output)
    return success, error, agent_cls, script_runner, resolve_provider


# ------------------------------------------------------------------ 判据 ① / ②


def test_a_round_without_a_recipient_never_starts(home):
    """缺收件人 ⇒ 什么都没发生：连 wake-gate 脚本都不跑，一个模型都不打。"""
    success, error, agent_cls, script_runner, _resolve = _fire(_job(recipient=None), home)

    assert success is False
    assert "收件人" in error
    agent_cls.assert_not_called()
    script_runner.assert_not_called()


def test_a_blank_recipient_is_the_same_as_missing(home):
    """空格串不等于有收件人——判据不许被一个空字符串绕过。"""
    success, _error, agent_cls, script_runner, _resolve = _fire(_job(recipient="   "), home)

    assert success is False
    agent_cls.assert_not_called()
    script_runner.assert_not_called()


def test_a_recipient_that_is_not_a_roster_row_is_refused(home):
    """查无此人 ⇒ 不起，且理由里带着那个名字（不许只报一句内部错误）。"""
    success, error, agent_cls, _script, _resolve = _fire(_job(recipient="nobody-here"), home)

    assert success is False
    assert "nobody-here" in error
    agent_cls.assert_not_called()


def test_an_archived_row_is_refused(home, tmp_path, monkeypatch):
    """归档那一行不是能认领这一轮的主（裁 19：归档是藏起来，不是删除）。"""
    entry = dataclasses.replace(
        AgentEntry(name="old-agenda", role="l2_agenda", profile="old-agenda"),
        archived=True,
    )
    monkeypatch.setenv("PLOBI_PROJECTS_CONFIG", _roster(tmp_path / "archived", entry))

    success, error, agent_cls, _script, _resolve = _fire(_job(recipient="old-agenda"), home)

    assert success is False
    assert "old-agenda" in error
    agent_cls.assert_not_called()


# ------------------------------------------------------------------ 判据 ③


def test_the_round_is_attributed_to_that_agent(home):
    """提示词里署名 + 输出文档里那一格写着主人 ⇒ 事后查得到是谁跑的。"""
    docs: list[str] = []
    success, error, agent_cls, _script, _resolve = _fire(_job(), home, docs=docs)

    assert success is True, error
    assert "日程秘书" in agent_cls.call_args.kwargs["ephemeral_system_prompt"]
    assert f"**Recipient:** {RECIPIENT}" in docs[0]


def test_the_session_id_shape_stays_cron_jobid_timestamp(home):
    """会话号形状是桌面的读路径：``SessionDB.list_cron_job_runs`` 按 ``cron_<job id>_`` 前缀
    做区间扫（``/api/cron/jobs/{id}/runs``）。收件人不许塞进这个 id——那会让每条作业的
    跑批履历在面上直接清空。"""
    _success, _error, agent_cls, _script, _resolve = _fire(_job(), home)

    assert agent_cls.call_args.kwargs["session_id"].startswith("cron_rcp-test_")


def test_no_internal_field_values_leak_into_the_identity_note(home):
    """裁 45：内部字段值进了提示词，模型就原样念给用户听。"""
    _success, _error, agent_cls, _script, _resolve = _fire(_job(), home)

    note = agent_cls.call_args.kwargs["ephemeral_system_prompt"]
    for internal in ("l2_agenda", "role", "profile", "slug"):
        assert internal not in note


def test_a_row_without_an_override_runs_on_the_home_default(home):
    """省略 provider/model = 没有覆盖（裁 42 §42.2 ⑥）：按本机默认解析，不折进角色路由。"""
    _success, _error, agent_cls, _script, resolve_provider = _fire(_job(), home)

    assert resolve_provider.call_args.kwargs["requested"] in (None, "")
    assert agent_cls.call_args.kwargs["model"] == "test-cron-default-model"


def test_a_row_that_declares_a_model_runs_on_that_model(home, tmp_path, monkeypatch):
    """那一行自己写了模型 ⇒ 这一轮用它。"""
    monkeypatch.setenv(
        "PLOBI_PROJECTS_CONFIG",
        _agenda_row(tmp_path, provider="rowprov", model="rowprov/row-model"),
    )
    monkeypatch.setattr(
        "plobi.agents.registry.provider_is_resolvable", lambda provider: provider == "rowprov"
    )

    _success, _error, agent_cls, _script, resolve_provider = _fire(_job(), home)

    assert resolve_provider.call_args.kwargs["requested"] == "rowprov"
    assert agent_cls.call_args.kwargs["model"] == "rowprov/row-model"


def test_a_sibling_row_does_not_borrow_the_override(home, tmp_path, monkeypatch):
    """可区分的那一半（裁 172）：覆盖只归写它那一行，别的行照旧按本机默认。"""
    monkeypatch.setenv(
        "PLOBI_PROJECTS_CONFIG",
        _roster(
            tmp_path,
            AgentEntry(name=RECIPIENT, role="l2_agenda", profile=RECIPIENT,
                       provider="rowprov", model="rowprov/row-model"),
            AgentEntry(name="other", role="l2_agenda", profile="other"),
        ),
    )
    monkeypatch.setattr(
        "plobi.agents.registry.provider_is_resolvable", lambda provider: True
    )

    _success, _error, agent_cls, _script, resolve_provider = _fire(
        _job(recipient="other"), home
    )

    assert resolve_provider.call_args.kwargs["requested"] in (None, "")
    assert agent_cls.call_args.kwargs["model"] == "test-cron-default-model"


def test_an_unresolvable_provider_on_the_row_is_dropped_not_fatal(home, tmp_path, monkeypatch):
    """底座认不出的 provider 当没写过：那一轮照旧起，只是按本机默认。"""
    monkeypatch.setenv(
        "PLOBI_PROJECTS_CONFIG",
        _agenda_row(tmp_path, provider="ghostprov", model="ghostprov/x"),
    )
    monkeypatch.setattr(
        "plobi.agents.registry.provider_is_resolvable", lambda provider: False
    )

    success, _error, agent_cls, _script, resolve_provider = _fire(_job(), home)

    assert success is True
    assert resolve_provider.call_args.kwargs["requested"] in (None, "")
    assert agent_cls.call_args.kwargs["model"] == "test-cron-default-model"


def test_a_pinned_row_does_not_trip_the_provider_drift_guard(home, tmp_path, monkeypatch):
    """那一行钉过的轴 = 用户钉死的选择，漂移闸门不许把它当成「跟着全局默认漂了」。"""
    monkeypatch.setenv(
        "PLOBI_PROJECTS_CONFIG",
        _agenda_row(tmp_path, provider="otherprov", model="other/model"),
    )
    monkeypatch.setattr(
        "plobi.agents.registry.provider_is_resolvable", lambda provider: True
    )

    success, error, agent_cls, _script, _resolve = _fire(
        _job(provider_snapshot="aigw", model_snapshot="workbuddy/hy3"), home
    )

    assert success is True, error
    agent_cls.assert_called_once()


def test_an_unpinned_job_still_trips_the_drift_guard(home):
    """闸门还在咬另一半：没有收件人覆盖、全局默认漂了，照旧一个钱都不花（#44585）。"""
    success, error, agent_cls, _script, _resolve = _fire(
        _job(provider_snapshot="aigw", model_snapshot="workbuddy/hy3"), home
    )

    assert success is False
    assert "unintended spend" in error
    agent_cls.assert_not_called()


def test_a_config_default_does_not_clobber_the_row(home, tmp_path, monkeypatch):
    """顶层那一份是本机默认，排在收件人那一行之后——否则「按角色跑」只剩一句提示词。"""
    monkeypatch.setenv(
        "PLOBI_PROJECTS_CONFIG",
        _agenda_row(tmp_path, provider="rowprov", model="rowprov/row-model"),
    )
    monkeypatch.setattr(
        "plobi.agents.registry.provider_is_resolvable", lambda provider: True
    )
    cfg_home = tmp_path / "cfg_home"
    cfg_home.mkdir()
    (cfg_home / "config.yaml").write_text(
        "model:\n  provider: homeprov\n  default: homeprov/home-model\n", encoding="utf-8"
    )

    success, _error, agent_cls, _script, _resolve = _fire(_job(), cfg_home)

    assert success is True
    assert agent_cls.call_args.kwargs["model"] == "rowprov/row-model"


# ------------------------------------------------------------------ 闸门不许扩大


def test_a_no_agent_job_still_fires_without_a_recipient(home):
    """采集 / 晨报 / 额度提醒那几条不经过 Agent——收件人闸门不许把它们一起拦掉。"""
    job = _job(recipient=None, no_agent=True, script="plobi_collect_sweep.py")
    with patch("cron.scheduler._plobi_home", home), \
         patch("cron.scheduler._run_job_script", return_value=(True, "采集完 3 条")) as script_runner, \
         patch("run_agent.AIAgent") as agent_cls:
        success, _output, final, error = run_job(job)

    assert success is True, error
    assert final == "采集完 3 条"
    script_runner.assert_called_once()
    agent_cls.assert_not_called()
