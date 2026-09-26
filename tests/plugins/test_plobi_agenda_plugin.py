"""The confirm interceptor must resolve replies without waking the agent."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest


def _load_plugin():
    name = "plobi_plugins.plobi_agenda"
    if name in sys.modules and hasattr(sys.modules[name], "register"):
        return sys.modules[name]

    if "plobi_plugins" not in sys.modules:
        namespace = types.ModuleType("plobi_plugins")
        namespace.__path__ = []  # type: ignore[attr-defined]
        sys.modules["plobi_plugins"] = namespace

    plugin_dir = Path(__file__).resolve().parents[2] / "plugins" / "plobi-agenda"
    spec = importlib.util.spec_from_file_location(
        name, plugin_dir / "__init__.py", submodule_search_locations=[str(plugin_dir)]
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    module.__package__ = name
    module.__path__ = [str(plugin_dir)]  # type: ignore[attr-defined]
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def wired(tmp_path):
    from plobi.agenda import dispatch as dispatch_module
    from plobi.agenda.service import AgendaService
    from plobi.notify import set_notifier
    from plobi.notify.base import RecordingNotifier

    service = AgendaService(tmp_path / "agenda.db")
    notifier = RecordingNotifier()
    dispatch_module.set_dispatcher(
        dispatch_module.PendingDispatcher(service=service, notifier=notifier)
    )
    set_notifier(notifier)

    yield SimpleNamespace(module=_load_plugin(), notifier=notifier, service=service)

    dispatch_module.set_dispatcher(None)
    set_notifier(None)


def _event(text: str):
    return SimpleNamespace(text=text, source=SimpleNamespace(platform="dingtalk", chat_id="c1"))


def test_registers_only_the_gateway_hook(wired):
    hooks: list[str] = []

    class Ctx:
        def register_hook(self, name, _callback):
            hooks.append(name)

        def register_tool(self, **_kwargs):
            raise AssertionError("this plugin must not add model tools")

    wired.module.register(Ctx())
    assert hooks == ["pre_gateway_dispatch"]


def test_confirmation_is_resolved_and_agent_is_skipped(wired):
    result = wired.service.ingest_candidate(title="组会", start_at="2026-08-26T16:00:00")

    outcome = wired.module._on_pre_gateway_dispatch(
        event=_event(f"确认 {result.event.confirm_seq}"), gateway=None
    )

    assert outcome == {"action": "skip", "reason": "agenda-confirmation"}
    assert wired.service.get(result.event.id).status == "confirmed"
    # The user still hears back, via the notifier when no adapter is available.
    assert any("已确认" in message for message in wired.notifier.sent)


def test_ordinary_message_falls_through_untouched(wired):
    assert wired.module._on_pre_gateway_dispatch(event=_event("明天几点开会？"), gateway=None) is None
    assert wired.notifier.sent == []


def test_empty_message_is_ignored(wired):
    assert wired.module._on_pre_gateway_dispatch(event=_event("   "), gateway=None) is None


def test_unknown_sequence_still_answers_and_skips(wired):
    outcome = wired.module._on_pre_gateway_dispatch(event=_event("确认 42"), gateway=None)

    assert outcome == {"action": "skip", "reason": "agenda-confirmation"}
    assert any("没有待确认" in message for message in wired.notifier.sent)


def test_resolver_failure_does_not_break_dispatch(wired, monkeypatch):
    from plobi.agenda import dispatch as dispatch_module

    class Exploding:
        def handle_reply(self, _text):
            raise RuntimeError("boom")

    monkeypatch.setattr(dispatch_module, "get_dispatcher", lambda: Exploding())

    # Falls through to normal dispatch instead of dropping the user's message.
    assert wired.module._on_pre_gateway_dispatch(event=_event("确认 1"), gateway=None) is None
