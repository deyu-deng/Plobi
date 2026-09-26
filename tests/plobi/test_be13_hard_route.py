"""WP-BE-13 — §8.2 narrow hard route (ARCH-RULINGS 2026-09-08 裁定 21.1).

Two frozen L1 utterances must reach ``plobi_secretary_ask``; everything else
(including any other sentence that merely contains 明天) must reach the model
untouched.

Run::

    C:/Users/xgbc/.workbuddy/binaries/python/envs/plobi-test/Scripts/python.exe \
        -m pytest tests/plobi/test_be13_hard_route.py --basetemp=.pytest-be13 -q
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
import types
from pathlib import Path

import pytest


# --------------------------------------------------------------------------
# Fixtures / loaders
# --------------------------------------------------------------------------

_PLUGIN_NAME = "hermes_plugins.plobi_north_star"


def _load_plugin():
    """Load the north-star plugin package the way the plugin manager does."""
    if _PLUGIN_NAME in sys.modules and hasattr(sys.modules[_PLUGIN_NAME], "register"):
        return sys.modules[_PLUGIN_NAME]

    if "hermes_plugins" not in sys.modules:
        namespace = types.ModuleType("hermes_plugins")
        namespace.__path__ = []  # type: ignore[attr-defined]
        sys.modules["hermes_plugins"] = namespace

    plugin_dir = Path(__file__).resolve().parents[2] / "plugins" / "plobi-north-star"
    spec = importlib.util.spec_from_file_location(
        _PLUGIN_NAME,
        plugin_dir / "__init__.py",
        submodule_search_locations=[str(plugin_dir)],
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    module.__package__ = _PLUGIN_NAME
    module.__path__ = [str(plugin_dir)]  # type: ignore[attr-defined]
    sys.modules[_PLUGIN_NAME] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def hard_route():
    """The pure-policy module, with the master-mode gate forced on."""
    _load_plugin()
    module = importlib.import_module(f"{_PLUGIN_NAME}.hard_route")
    original = module._master_mode_enabled
    module._master_mode_enabled = lambda: True
    yield module
    module._master_mode_enabled = original


# --------------------------------------------------------------------------
# 1. Hit table — the two frozen utterances (and only their allowed variants)
# --------------------------------------------------------------------------

REFRESH_AGENDA_VARIANTS = (
    "明天的日常安排是什么",
    "明天的日常安排是什么？",  # full-width question mark
    "明天的日常安排是什么?",  # half-width question mark
    "  明天的日常安排是什么  ",  # leading/trailing ASCII whitespace
    "　　明天的日常安排是什么？　",  # ideographic space + full-width mark
    "\n明天的日常安排是什么?\t",
)

WRITE_BRIEFING_VARIANTS = (
    "根据明天的日程写一段早报",
    "根据明天的日程写一段早报？",
    "根据明天的日程写一段早报?",
    "  根据明天的日程写一段早报  ",
    "　根据明天的日程写一段早报？",
)


@pytest.mark.parametrize("text", REFRESH_AGENDA_VARIANTS)
def test_sentence_one_matches_refresh_agenda(hard_route, text):
    assert hard_route.match_frozen_utterance(text) == "refresh_agenda"


@pytest.mark.parametrize("text", WRITE_BRIEFING_VARIANTS)
def test_sentence_two_matches_write_briefing(hard_route, text):
    assert hard_route.match_frozen_utterance(text) == "write_briefing"


# --------------------------------------------------------------------------
# 2. Miss table — no keyword routing, no blanket 明天 interception
# --------------------------------------------------------------------------

MUST_NOT_MATCH = (
    "明天几点开会？",  # the task's explicit counter-example
    "明天天气怎么样？",
    "帮我看看明天的日程是不是空的",
    "明天的日常安排是什么来着，我记不清了",
    "根据明天的日程写一段早报，再发到钉钉",
    "帮我看看这个 bug",
    "",
    "   ",
    "？",
)


@pytest.mark.parametrize("text", MUST_NOT_MATCH)
def test_other_utterances_are_not_intercepted(hard_route, text):
    assert hard_route.match_frozen_utterance(text) is None


@pytest.mark.parametrize("value", [None, 123, ["明天的日常安排是什么"], {"text": "明天的日常安排是什么"}])
def test_non_string_input_is_not_intercepted(hard_route, value):
    assert hard_route.match_frozen_utterance(value) is None


def test_normalize_strips_only_one_trailing_question_mark(hard_route):
    assert hard_route.normalize_utterance("  x？  ") == "x"
    assert hard_route.normalize_utterance("x?") == "x"
    assert hard_route.normalize_utterance("x？？") == "x？"


# --------------------------------------------------------------------------
# 3. Rewrite payload
# --------------------------------------------------------------------------


def test_rewrite_pins_the_tool_and_keeps_the_original_words(hard_route):
    text = hard_route.build_rewrite("明天的日常安排是什么？", "refresh_agenda")

    assert "plobi_secretary_ask" in text
    assert 'intent="refresh_agenda"' in text
    assert 'user_text="明天的日常安排是什么？"' in text
    # Anti-fabrication clause is part of the forced instruction.
    assert "禁止编造" in text
    assert "chatlog" in text


def test_rewrite_rejects_unknown_intent(hard_route):
    with pytest.raises(ValueError):
        hard_route.build_rewrite("x", "delete_everything")


# --------------------------------------------------------------------------
# 4. Hook behaviour (seam contract)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,intent",
    [
        ("明天的日常安排是什么", "refresh_agenda"),
        ("根据明天的日程写一段早报", "write_briefing"),
    ],
)
def test_hook_rewrites_both_frozen_utterances(hard_route, text, intent):
    outcome = hard_route.on_pre_prompt_submit(text=text, session_id="s1")

    assert isinstance(outcome, dict)
    assert outcome["action"] == "rewrite"
    assert outcome["text"] == hard_route.build_rewrite(text, intent)


def test_hook_is_a_noop_for_a_third_sentence(hard_route):
    assert hard_route.on_pre_prompt_submit(text="帮我看看这个 bug", session_id="s1") is None


def test_hook_is_a_noop_when_the_secretary_tool_is_not_served(hard_route):
    hard_route._master_mode_enabled = lambda: False

    assert hard_route.on_pre_prompt_submit(text="明天的日常安排是什么", session_id="s1") is None


def test_hook_is_a_noop_for_blank_text(hard_route):
    assert hard_route.on_pre_prompt_submit(text="   ", session_id="s1") is None
    assert hard_route.on_pre_prompt_submit(text=None, session_id="s1") is None


# --------------------------------------------------------------------------
# 5. Seam: the hook name is legal, and tui_gateway actually applies it
# --------------------------------------------------------------------------


def test_valid_hooks_include_pre_prompt_submit():
    from hermes_cli.plugins import VALID_HOOKS

    assert "pre_prompt_submit" in VALID_HOOKS


def test_plugin_registers_the_prompt_submit_hook():
    hooks: list[str] = []

    class Ctx:
        def register_hook(self, name, _callback):
            hooks.append(name)

        def register_tool(self, **_kwargs):
            return None

    _load_plugin().register(Ctx())
    assert "pre_prompt_submit" in hooks


@pytest.fixture()
def seam():
    return importlib.import_module("tui_gateway.server")


def test_prompt_submit_seam_applies_a_plugin_rewrite(seam, monkeypatch):
    import hermes_cli.plugins as plugins_module

    seen: list[dict] = []

    def _fake_invoke_hook(name, **kwargs):
        seen.append({"name": name, **kwargs})
        if kwargs.get("text", "").strip() == "明天的日常安排是什么？":
            return [{"action": "rewrite", "text": "REWRITTEN"}]
        return []

    monkeypatch.setattr(plugins_module, "invoke_hook", _fake_invoke_hook)

    assert seam._rewrite_prompt_submit_text("s1", "明天的日常安排是什么？") == "REWRITTEN"
    assert seen[0]["name"] == "pre_prompt_submit"
    assert seen[0]["session_id"] == "s1"


@pytest.mark.parametrize(
    "hook_result",
    [
        [],
        [None],
        ["not-a-dict"],
        [{"action": "allow"}],
        [{"action": "rewrite", "text": "   "}],
        [{"action": "rewrite"}],
    ],
)
def test_prompt_submit_seam_leaves_text_untouched_without_a_rewrite(
    seam, monkeypatch, hook_result
):
    import hermes_cli.plugins as plugins_module

    monkeypatch.setattr(
        plugins_module, "invoke_hook", lambda name, **kwargs: hook_result
    )

    assert seam._rewrite_prompt_submit_text("s1", "帮我看看这个 bug") == "帮我看看这个 bug"


def test_prompt_submit_seam_is_fail_open(seam, monkeypatch):
    import hermes_cli.plugins as plugins_module

    def _boom(name, **kwargs):
        raise RuntimeError("plugin exploded")

    monkeypatch.setattr(plugins_module, "invoke_hook", _boom)

    assert seam._rewrite_prompt_submit_text("s1", "明天的日常安排是什么") == "明天的日常安排是什么"


@pytest.mark.parametrize("payload", [None, 42, "", "   "])
def test_prompt_submit_seam_ignores_non_text_payloads(seam, monkeypatch, payload):
    import hermes_cli.plugins as plugins_module

    def _never_called(name, **kwargs):  # pragma: no cover - must not fire
        raise AssertionError("hook must not fire for a non-text payload")

    monkeypatch.setattr(plugins_module, "invoke_hook", _never_called)

    assert seam._rewrite_prompt_submit_text("s1", payload) == payload
