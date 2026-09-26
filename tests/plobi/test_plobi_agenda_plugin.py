"""plobi-agenda 插件：确认拦截、用法提示、绝不吞正常对话。"""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path

import pytest

from plobi.agenda import dispatch as agenda_dispatch
from plobi.agenda.dispatch import PendingDispatcher
from plobi.agenda.service import AgendaService
from plobi.notify.base import NullNotifier


def _load_plugin():
    """插件目录带连字符（plobi-agenda），不能按包名导入，按路径加载。"""
    init = (
        Path(__file__).resolve().parents[2] / "plugins" / "plobi-agenda" / "__init__.py"
    )
    spec = importlib.util.spec_from_file_location("plobi_agenda_plugin", init)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


plugin = _load_plugin()


class Adapter:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, chat_id, content, metadata=None):
        self.sent.append(content)


class Source:
    platform = "dingtalk"
    chat_id = "chat-1"


class Event:
    def __init__(self, text: str) -> None:
        self.text = text
        self.source = Source()
        self.metadata = None


class Gateway:
    def __init__(self, adapter: Adapter) -> None:
        self.adapters = {"dingtalk": adapter}


@pytest.fixture()
def wired(tmp_path, monkeypatch):
    monkeypatch.setattr(
        agenda_dispatch,
        "_DEFAULT",
        PendingDispatcher(service=AgendaService(tmp_path / "agenda.db"), notifier=NullNotifier()),
    )


def _run(wired, text: str):
    adapter = Adapter()
    gateway = Gateway(adapter)

    async def scenario():
        result = plugin._on_pre_gateway_dispatch(event=Event(text), gateway=gateway)
        await asyncio.sleep(0)  # 让 create_task 的回执协程跑完
        return result, adapter.sent

    return asyncio.run(scenario())


def test_valid_reply_is_intercepted_and_acked(wired):
    svc = agenda_dispatch.get_dispatcher().service
    result = svc.ingest_candidate(title="组会", start_at="2026-08-26T16:00:00")

    action, sent = _run(wired, f"确认 {result.event.confirm_seq}")
    assert action == {"action": "skip", "reason": "agenda-confirmation"}
    assert sent and "已确认" in sent[0]
    assert svc.get(result.event.id).status == "confirmed"


def test_bare_protocol_verb_gets_usage_hint(wired):
    action, sent = _run(wired, "确认")
    assert action == {"action": "skip", "reason": "agenda-usage-hint"}
    assert sent == [plugin._USAGE_HINT]


def test_protocol_verb_with_bad_number_gets_usage_hint(wired):
    action, sent = _run(wired, "忽略12。")
    assert action == {"action": "skip", "reason": "agenda-usage-hint"}
    assert sent == [plugin._USAGE_HINT]


@pytest.mark.parametrize(
    "text",
    [
        "确认一下明天的时间",
        "帮我取消今天的会议",
        "确定要退掉这门课吗",
        "明天几点开会？",
    ],
)
def test_ordinary_chat_reaches_the_agent_untouched(wired, text):
    action, sent = _run(wired, text)
    assert action is None
    assert sent == []
