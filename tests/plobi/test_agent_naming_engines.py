"""命名翻译的引擎顺序（用户 2026-11-22 两次裁决）。

自维护词表已删：这里同时钉住「词表不许回来」——它替用户决定「日程秘书」该叫
哪个英文，猜错的名字会落到门牌与磁盘目录上。

同日第二刀：「Google 只是举了一个例子，你有没有更好的引擎呢？」于是分成两条
通道，顺序**不一样**，这是这组测试的重点：

* 输入时（force=False）要快：DeepL → Google → MyMemory → 本地 → LLM 兜底；
* 点刷新时（force=True）要好：LLM 顶到最前，机翻退居其后。
"""

from __future__ import annotations

import pytest

from plobi.agents import naming


@pytest.fixture(autouse=True)
def _no_ambient_engines(monkeypatch):
    """默认让本地引擎没配、DeepL 没 key；每个用例自己决定接哪条。"""
    monkeypatch.delenv(naming._LOCAL_TRANSLATE_URL_ENV, raising=False)
    monkeypatch.delenv(naming._DEEPL_KEY_ENV, raising=False)


def _spy(calls: list[str], label: str, result: str):
    def _run(*_args, **_kwargs):
        calls.append(label)

        return result

    return _run


def test_ascii_name_is_returned_as_is(monkeypatch):
    def _boom(*_args, **_kwargs):
        raise AssertionError("ASCII 名字不该走到任何翻译引擎")

    monkeypatch.setattr(naming, "translate_local", _boom)
    monkeypatch.setattr(naming, "translate_google", _boom)
    monkeypatch.setattr(naming, "translate_mymemory", _boom)
    monkeypatch.setattr(naming, "translate_deepl", _boom)
    monkeypatch.setattr(naming, "translate_llm", _boom)

    assert naming.english_name_for("My Project") == "My Project"


def test_typing_channel_prefers_machine_translation(monkeypatch):
    """边打边翻时不许先等大模型：DeepL / Google 出结果就立刻返回。"""
    calls: list[str] = []

    monkeypatch.setattr(naming, "translate_deepl", _spy(calls, "deepl", ""))
    monkeypatch.setattr(naming, "translate_google", _spy(calls, "google", "Agenda Secretary"))
    monkeypatch.setattr(naming, "translate_mymemory", _spy(calls, "mymemory", "Wrong"))
    monkeypatch.setattr(naming, "translate_local", _spy(calls, "local", "Wrong"))
    monkeypatch.setattr(naming, "translate_llm", _spy(calls, "llm", "Wrong"))

    assert naming.english_name_for("日程秘书") == "Agenda Secretary"
    # DeepL 先问一句（没 key 就是空），Google 命中即收 —— LLM 一次都不许被打。
    assert calls == ["deepl", "google"]


def test_deepl_is_the_first_machine_engine_when_key_present(monkeypatch):
    calls: list[str] = []

    monkeypatch.setattr(naming, "translate_deepl", _spy(calls, "deepl", "Thermal Lab"))
    monkeypatch.setattr(naming, "translate_google", _spy(calls, "google", "Wrong"))
    monkeypatch.setattr(naming, "translate_mymemory", _spy(calls, "mymemory", "Wrong"))
    monkeypatch.setattr(naming, "translate_llm", _spy(calls, "llm", "Wrong"))

    assert naming.english_name_for("热管理实验室") == "Thermal Lab"
    assert calls == ["deepl"]


def test_mymemory_covers_when_google_is_blocked(monkeypatch):
    calls: list[str] = []

    monkeypatch.setattr(naming, "translate_deepl", _spy(calls, "deepl", ""))
    monkeypatch.setattr(naming, "translate_google", _spy(calls, "google", ""))
    monkeypatch.setattr(naming, "translate_mymemory", _spy(calls, "mymemory", "Winter Design"))
    monkeypatch.setattr(naming, "translate_local", _spy(calls, "local", "Wrong"))
    monkeypatch.setattr(naming, "translate_llm", _spy(calls, "llm", "Wrong"))

    assert naming.english_name_for("暖冬视觉设计") == "Winter Design"
    assert calls == ["deepl", "google", "mymemory"]


def test_local_engine_is_the_last_machine_resort(monkeypatch):
    calls: list[str] = []

    monkeypatch.setattr(naming, "translate_deepl", _spy(calls, "deepl", ""))
    monkeypatch.setattr(naming, "translate_google", _spy(calls, "google", ""))
    monkeypatch.setattr(naming, "translate_mymemory", _spy(calls, "mymemory", ""))
    monkeypatch.setattr(naming, "translate_local", _spy(calls, "local", "Thermal Lab"))
    monkeypatch.setattr(naming, "translate_llm", _spy(calls, "llm", "Wrong"))

    assert naming.english_name_for("热管理实验室") == "Thermal Lab"
    assert calls == ["deepl", "google", "mymemory", "local"]


def test_llm_is_only_a_safety_net_while_typing(monkeypatch):
    """机翻全灭时才轮到 LLM，而且只给 3 秒（不让输入框干等）。"""
    seen: dict[str, object] = {}

    monkeypatch.setattr(naming, "translate_deepl", _spy([], "deepl", ""))
    monkeypatch.setattr(naming, "translate_google", _spy([], "google", ""))
    monkeypatch.setattr(naming, "translate_mymemory", _spy([], "mymemory", ""))
    monkeypatch.setattr(naming, "translate_local", _spy([], "local", ""))

    def _llm(_text, *, timeout=None):
        seen["timeout"] = timeout

        return "Nuandong Design"

    monkeypatch.setattr(naming, "translate_llm", _llm)

    assert naming.english_name_for("暖冬视觉设计") == "Nuandong Design"
    assert seen["timeout"] == naming._FALLBACK_LLM_TIMEOUT


def test_refresh_button_puts_llm_first(monkeypatch):
    """用户点了刷新：说明上次翻得不对，先问懂语境的那条路。"""
    calls: list[str] = []

    monkeypatch.setattr(naming, "translate_llm", _spy(calls, "llm", "Agenda Secretary"))
    monkeypatch.setattr(naming, "translate_deepl", _spy(calls, "deepl", "Wrong"))
    monkeypatch.setattr(naming, "translate_google", _spy(calls, "google", "Wrong"))

    assert naming.english_name_for("日程秘书", force=True) == "Agenda Secretary"
    assert calls == ["llm"]


def test_refresh_falls_back_to_machine_translation(monkeypatch):
    calls: list[str] = []

    monkeypatch.setattr(naming, "translate_llm", _spy(calls, "llm", ""))
    monkeypatch.setattr(naming, "translate_deepl", _spy(calls, "deepl", ""))
    monkeypatch.setattr(naming, "translate_google", _spy(calls, "google", "Agenda Secretary"))
    monkeypatch.setattr(naming, "translate_mymemory", _spy(calls, "mymemory", "Wrong"))

    assert naming.english_name_for("日程秘书", force=True) == "Agenda Secretary"
    assert calls == ["llm", "deepl", "google"]


def test_every_engine_failing_yields_empty_string(monkeypatch):
    monkeypatch.setattr(naming, "translate_local", lambda text, **_kw: "")
    monkeypatch.setattr(naming, "translate_google", lambda text, **_kw: "")
    monkeypatch.setattr(naming, "translate_mymemory", lambda text, **_kw: "")
    monkeypatch.setattr(naming, "translate_deepl", lambda text, **_kw: "")
    monkeypatch.setattr(naming, "translate_llm", lambda text, **_kw: "")

    assert naming.english_name_for("暖冬视觉设计") == ""


def test_glossary_is_gone():
    assert not hasattr(naming, "glossary_english_name")
    assert not hasattr(naming, "_GLOSSARY")
