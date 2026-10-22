"""Tests for the ``plobi prompt-size`` diagnostic (issue #34667)."""

import json
import sys
from types import SimpleNamespace

import pytest

from plobi_cli.prompt_size import (
    _SKILLS_BLOCK_RE,
    _build_inspection_agent,
    compute_prompt_breakdown,
    render_breakdown,
)


def _seed_memory(plobi_home, memory_text="", user_text=""):
    """Seed the shared memory root the way 裁定 181 lays it out.

    ``memory/USER.md`` is one shared copy; an agent's own notes live per identity
    under ``memory/agents/<slug>/MEMORY.md`` — the hermetic test environment sets
    no ``PLOBI_AGENT_ID``, so the identity resolves to the L1 slug.
    """
    mem_dir = plobi_home / "memory"
    mem_dir.mkdir(parents=True, exist_ok=True)
    if memory_text:
        own = mem_dir / "agents" / "l1"
        own.mkdir(parents=True, exist_ok=True)
        (own / "MEMORY.md").write_text(memory_text, encoding="utf-8")
    if user_text:
        (mem_dir / "USER.md").write_text(user_text, encoding="utf-8")


def _seed_skill(plobi_home, name, description):
    skill_dir = plobi_home / "skills" / "demo" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n# {name}\nbody\n",
        encoding="utf-8",
    )


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    plobi_home = tmp_path / ".plobi"
    plobi_home.mkdir()
    monkeypatch.setenv("PLOBI_HOME", str(plobi_home))
    monkeypatch.chdir(tmp_path)  # avoid picking up the repo's AGENTS.md
    return plobi_home


def test_breakdown_keys_and_shape(isolated_home):
    """The breakdown exposes every documented key with int byte/char counts."""
    data = compute_prompt_breakdown("cli")
    assert set(data) >= {
        "platform",
        "model",
        "system_prompt",
        "skills_index",
        "memory",
        "user_profile",
        "tools",
        "sections",
    }
    assert data["platform"] == "cli"
    for key in ("system_prompt", "skills_index", "memory", "user_profile"):
        assert data[key]["bytes"] >= 0
        assert data[key]["chars"] >= 0
    assert data["tools"]["count"] >= 0
    assert data["tools"]["json_bytes"] >= 0
    # System prompt is non-trivial even with empty home (identity + guidance).
    assert data["system_prompt"]["bytes"] > 0


def test_budget_verdict_is_computed_on_the_real_offline_path(isolated_home):
    """离线也能出判决：数字来自真拼一次前缀，不是手填样例。"""
    data = compute_prompt_breakdown("cli")
    b = data["budget"]
    assert set(b) >= {
        "used_tokens", "system_prompt_tokens", "tools_tokens",
        "limit_tokens", "over", "biggest",
    }
    assert b["used_tokens"] == b["system_prompt_tokens"] + b["tools_tokens"]
    assert b["used_tokens"] > 0 and b["limit_tokens"] > 0
    assert b["over"] is (b["used_tokens"] > b["limit_tokens"])
    # 最大一块必须点名（"超了"却不说是哪块等于没报）
    assert b["biggest"]["tokens"] > 0


def test_rendered_report_shows_the_ceiling(isolated_home):
    text = render_breakdown(compute_prompt_breakdown("cli"))
    assert "Per-call prefix" in text and "上限" in text
    assert "下界" in text, "粗估对中文偏低这件事必须写在报告里，不能装作是精确值"


def test_runs_offline_without_credentials(isolated_home, monkeypatch):
    """No provider credentials configured → still produces a breakdown."""
    for var in ("OPENROUTER_API_KEY", "OPENAI_API_KEY", "NOUS_API_KEY",
                "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    data = compute_prompt_breakdown("cli")
    assert data["system_prompt"]["bytes"] > 0


def test_inspection_agent_uses_resolved_platform_toolsets(monkeypatch):
    """Inspection must match real CLI tool resolution, including disables."""
    captured = {}

    class FakeAIAgent:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    cfg = {
        "model": {"default": "test/model"},
        "agent": {"disabled_toolsets": ["memory"]},
    }

    monkeypatch.setitem(
        sys.modules,
        "run_agent",
        SimpleNamespace(AIAgent=FakeAIAgent),
    )
    monkeypatch.setattr("plobi_cli.config.load_config", lambda: cfg)
    monkeypatch.setattr(
        "plobi_cli.tools_config._get_platform_tools",
        lambda passed_cfg, platform: {"terminal", "file"},
    )

    _build_inspection_agent("cli")

    assert captured["model"] == "test/model"
    assert captured["platform"] == "cli"
    assert captured["enabled_toolsets"] == ["file", "terminal"]
    assert captured["disabled_toolsets"] == ["memory"]


def test_blank_slate_prompt_size_counts_only_minimal_tools(isolated_home):
    """Blank Slate prompt-size should report file + terminal schemas only."""
    from plobi_cli.config import save_config
    from plobi_cli.setup import (
        _blank_slate_minimal_toolsets,
        _blank_slate_minimize_config,
    )

    cfg = {"model": {"default": "MiniMax-M2.7"}}
    _blank_slate_minimal_toolsets(cfg)
    _blank_slate_minimize_config(cfg)
    save_config(cfg)

    data = compute_prompt_breakdown("cli")

    assert data["tools"]["count"] == 6


def test_skills_index_reflects_installed_skills(isolated_home):
    """Installing a skill makes the skills-index block non-empty.

    Note: the skills prompt is cached per-process (in-process LRU + disk
    snapshot), so we seed the skill BEFORE the first build rather than
    comparing before/after within one process.
    """
    _seed_skill(isolated_home, "hello", "a demo skill for size testing")
    data = compute_prompt_breakdown("cli")
    assert data["skills_index"]["bytes"] > 0


def test_memory_and_profile_are_attributed(isolated_home):
    """Memory and user-profile blocks are measured separately."""
    _seed_memory(
        isolated_home,
        memory_text="Project uses pytest.\n",
        user_text="User is a developer.\n",
    )
    data = compute_prompt_breakdown("cli")
    assert data["memory"]["bytes"] > 0
    assert data["user_profile"]["bytes"] > 0


def test_skills_block_regex_matches_tagged_block():
    text = "preamble\n<available_skills>\n  cat:\n    - a: b\n</available_skills>\ntail"
    m = _SKILLS_BLOCK_RE.search(text)
    assert m is not None
    assert m.group(0).startswith("<available_skills>")
    assert m.group(0).endswith("</available_skills>")


def test_render_breakdown_is_plain_text(isolated_home):
    data = compute_prompt_breakdown("cli")
    out = render_breakdown(data)
    assert "System prompt total" in out
    assert "skills index" in out
    assert "Tool schemas" in out
    # Plain text — no JSON braces leaking in.
    assert not out.strip().startswith("{")


def test_json_serializable(isolated_home):
    data = compute_prompt_breakdown("cli")
    # Round-trips cleanly for ``--json`` output.
    assert json.loads(json.dumps(data)) == json.loads(json.dumps(data))
