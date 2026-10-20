"""弹窗那一格「谁来跑这件事」到后端为止的管道（WP-CRON-RECIPIENT 屏幕上这一半）。

`cron/jobs.py::create_job` 早就有 `recipient` 这个参数、`scheduler.py` 也已经会
「解析不到名册那一行就不起」——**缺的一直是这一层**：桌面 POST 上来的 recipient 从没被
转进 `create_job(...)`。所以这里钉的是"参数有没有带上"，不是 cron 本身的行为（那半有
`tests/cron/test_cron_recipient.py` 在 Butler 那一笔下）。

全程不起真 cron、不碰真 jobs.json、不打模型。
"""

from __future__ import annotations

import pytest

from plobi_cli import web_server as ws


@pytest.fixture
def captured(monkeypatch):
    """把「按 profile 起一个 cron 调用」这一跳接掉，只留参数。"""
    calls: list[dict] = []

    def fake_call(profile_name, method, **kwargs):
        calls.append({"method": method, "profile": profile_name, **kwargs})
        return {"id": "job_1", "name": kwargs.get("name") or ""}

    monkeypatch.setattr(ws, "_call_cron_for_profile", fake_call)
    monkeypatch.setattr(ws, "_cron_profile_home", lambda profile: (profile or "default", None))
    monkeypatch.setattr(ws, "_validate_dashboard_cron_context_from", lambda context_from, profile_name: None)
    monkeypatch.setattr(ws, "_validate_dashboard_cron_effective_job", lambda effective: None)
    monkeypatch.setattr(ws, "_normalize_dashboard_cron_script", lambda script, home: script)
    return calls


def test_recipient_reaches_create_job(captured):
    body = ws.CronJobCreate(prompt="跑一下", schedule="0 9 * * *", recipient="resonote")

    ws._create_cron_job_sync(body, profile="default")

    assert captured[0]["method"] == "create_job"
    assert captured[0]["recipient"] == "resonote"


def test_no_recipient_is_absent_not_default(captured):
    """不选＝这个键不给，不是 'default'——写成一个不存在的 Agent 名字比不写更糟。"""
    body = ws.CronJobCreate(prompt="跑一下", schedule="0 9 * * *")

    ws._create_cron_job_sync(body, profile="default")

    assert captured[0]["recipient"] is None


def test_blank_recipient_is_normalized_to_absent(captured):
    body = ws.CronJobCreate(prompt="跑一下", schedule="0 9 * * *", recipient="   ")

    ws._create_cron_job_sync(body, profile="default")

    assert captured[0]["recipient"] is None


def test_delivery_channel_is_still_its_own_field(captured):
    """「谁来跑」与「送到哪」是两格，别互相顶替。"""
    body = ws.CronJobCreate(prompt="跑一下", schedule="0 9 * * *", recipient="aura", deliver="origin")

    ws._create_cron_job_sync(body, profile="default")

    assert captured[0]["recipient"] == "aura"
    assert captured[0]["deliver"] == "origin"


def test_update_normalizer_treats_blank_recipient_as_unset():
    normalized = ws._normalize_dashboard_cron_updates({"recipient": "  ", "model": " x "}, None)

    assert normalized["recipient"] is None
    assert normalized["model"] == "x"


def test_update_keeps_a_recipient_that_was_cleared_to_null():
    """显式 null ＝ 改回不指定；这条与「没带这个键」必须分得开（省略＝不动）。"""
    normalized = ws._normalize_dashboard_cron_updates({"recipient": None}, None)

    assert "recipient" in normalized
    assert normalized["recipient"] is None
