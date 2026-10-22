"""固定前缀预算：系统提示 + 工具 schema 的每一轮固定成本要有一条上限。

判据三条：上限怎么算（配置 → 窗口 × 10% → 地板）、超线时点不点名最大那块、
以及这一格在 DEFAULT_CONFIG 里存在（用户能自己放宽，不用改代码）。
"""

from typing import Optional

import pytest

from agent.prompt_builder import (
    _PROMPT_BUDGET_FLOOR_TOKENS,
    _PROMPT_BUDGET_WINDOW_FRACTION,
    get_prompt_max_tokens,
    prompt_budget_verdict,
)


@pytest.fixture
def no_config_override(monkeypatch):
    import plobi_cli.config as cfg

    monkeypatch.setattr(cfg, "load_config", lambda *a, **k: {})


@pytest.mark.parametrize(
    "window,expected",
    [
        (None, _PROMPT_BUDGET_FLOOR_TOKENS),          # 窗口未知 → 只用地板
        (0, _PROMPT_BUDGET_FLOOR_TOKENS),
        (-5, _PROMPT_BUDGET_FLOOR_TOKENS),
        (128_000, int(128_000 * _PROMPT_BUDGET_WINDOW_FRACTION)),
        (1_000_000, int(1_000_000 * _PROMPT_BUDGET_WINDOW_FRACTION)),
    ],
)
def test_limit_derivation(no_config_override, window: Optional[int], expected: int):
    assert get_prompt_max_tokens(window) == expected


def test_small_window_still_gets_the_floor(no_config_override):
    """8K 窗口的模型按 10% 只有 800 token —— 那连身份段都装不下，所以地板兜住。"""
    assert get_prompt_max_tokens(8_000) == _PROMPT_BUDGET_FLOOR_TOKENS


def test_config_value_wins_over_derivation(monkeypatch):
    import plobi_cli.config as cfg

    monkeypatch.setattr(cfg, "load_config", lambda *a, **k: {"prompt_max_tokens": 9_000})
    assert get_prompt_max_tokens(1_000_000) == 9_000


@pytest.mark.parametrize("bad", [0, -1, "12k", None, True])
def test_garbage_config_falls_back_to_derivation(monkeypatch, bad):
    import plobi_cli.config as cfg

    monkeypatch.setattr(cfg, "load_config", lambda *a, **k: {"prompt_max_tokens": bad})
    assert get_prompt_max_tokens(128_000) == int(128_000 * _PROMPT_BUDGET_WINDOW_FRACTION)


def test_prompt_max_tokens_is_a_configurable_key():
    from plobi_cli.config import DEFAULT_CONFIG

    assert "prompt_max_tokens" in DEFAULT_CONFIG
    assert DEFAULT_CONFIG["prompt_max_tokens"] is None, "默认＝按窗口推导，不写死数值"


def test_bool_is_not_a_valid_limit_on_either_key(monkeypatch):
    """YAML 里手滑写 ``true`` 不能被读成 1 token——两个上限键同一判据。"""
    import plobi_cli.config as cfg
    from agent.prompt_builder import _get_context_file_max_chars, get_prompt_max_tokens

    monkeypatch.setattr(
        cfg, "load_config",
        lambda *a, **k: {"context_file_max_chars": True, "prompt_max_tokens": True},
    )
    assert get_prompt_max_tokens(128_000) == 12_800
    assert _get_context_file_max_chars(128_000) > 1


def test_verdict_reports_math_and_no_directionless_alarm(no_config_override):
    big = "x" * (4 * 30_000)          # ≈30,000 tokens
    small = "y" * 400                 # ≈100 tokens
    v = prompt_budget_verdict(
        big, small, context_length=128_000,
        sections=[("AGENTS.md 与工作目录文件", big), ("tool schemas", small)],
    )
    assert v["system_prompt_tokens"] == 30_000 and v["tools_tokens"] == 100
    assert v["used_tokens"] == 30_100
    assert v["limit_tokens"] == 12_800
    assert v["over"] is True
    assert v["biggest"]["label"] == "AGENTS.md 与工作目录文件"
    assert v["biggest"]["tokens"] == 30_000


def test_verdict_stays_quiet_inside_budget(no_config_override):
    v = prompt_budget_verdict("short", "", context_length=128_000, sections=[])
    assert v["over"] is False
    assert v["biggest"] == {"label": "", "tokens": 0}


def test_verdict_never_divides_by_zero_on_unknown_window(no_config_override):
    v = prompt_budget_verdict("x" * 4_000, "y" * 4_000, context_length=None)
    assert v["limit_tokens"] == _PROMPT_BUDGET_FLOOR_TOKENS
    assert v["context_length"] is None
    assert v["over"] is False        # 8,000 tokens < 12,000 地板
