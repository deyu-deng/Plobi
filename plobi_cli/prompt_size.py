"""Prompt-size diagnostic: ``plobi prompt-size``.

Reports a byte/char breakdown of the system prompt the agent would build for
a fresh session — system prompt total, the ``<available_skills>`` index,
memory + user profile, and tool-schema JSON. Lets users see where their fixed
prompt budget goes (issue #34667) without parsing a saved session JSON by hand.

The diagnostic builds a real inspection agent (so the numbers match what
actually ships on the wire) but never makes a network call: it passes dummy
credentials so ``AIAgent.__init__`` takes the direct-construction path, then
calls ``build_system_prompt_parts`` / inspects ``agent.tools`` offline.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Tuple

# The skills index is wrapped in this tag pair inside the stable tier.
_SKILLS_BLOCK_RE = re.compile(r"<available_skills>.*?</available_skills>", re.DOTALL)


def _bytes(s: str) -> int:
    return len(s.encode("utf-8"))


def _build_inspection_agent(platform: str) -> Any:
    """Construct an offline AIAgent for prompt inspection.

    Dummy ``api_key`` + ``base_url`` force the direct-construction path in
    ``run_agent.py`` (no provider auto-detection, no network). Toolsets and
    platform come from the caller so the breakdown matches a real session.
    """
    from run_agent import AIAgent
    from plobi_cli.config import load_config
    from plobi_cli.tools_config import _get_platform_tools

    cfg = load_config()
    model_cfg = cfg.get("model", {}) if isinstance(cfg.get("model"), dict) else {}
    model = model_cfg.get("default") or model_cfg.get("model") or ""

    # Resolve platform-specific toolsets the same way the gateway does.
    enabled_toolsets = sorted(_get_platform_tools(cfg, platform))
    agent_cfg = cfg.get("agent") or {}
    disabled_toolsets = agent_cfg.get("disabled_toolsets") or None

    return AIAgent(
        model=model,
        api_key="inspect-only",
        base_url="https://openrouter.ai/api/v1",
        quiet_mode=True,
        save_trajectories=False,
        platform=platform,
        enabled_toolsets=enabled_toolsets,
        disabled_toolsets=disabled_toolsets,
    )


def compute_prompt_breakdown(platform: str = "cli") -> Dict[str, Any]:
    """Return a dict of prompt-size measurements for a fresh session.

    Keys: ``system_prompt`` (chars/bytes), ``skills_index``, ``memory``,
    ``user_profile``, ``tools`` (count + json bytes), and ``sections`` (a list
    of (label, chars, bytes) for the three prompt tiers).
    """
    from agent.system_prompt import build_system_prompt, build_system_prompt_parts

    agent = _build_inspection_agent(platform)

    parts = build_system_prompt_parts(agent)
    full = build_system_prompt(agent)

    stable = parts.get("stable", "")
    context = parts.get("context", "")
    volatile = parts.get("volatile", "")

    # Skills index — the <available_skills> block (the largest single block
    # when many skills are installed). Measured inside the stable tier.
    skills_match = _SKILLS_BLOCK_RE.search(stable)
    skills_index = skills_match.group(0) if skills_match else ""

    # Memory + user profile live in the volatile tier. We re-derive their
    # blocks directly from the memory store so the numbers are attributable
    # even though they're joined into ``volatile``.
    memory_block = ""
    user_block = ""
    store = getattr(agent, "_memory_store", None)
    if store is not None:
        try:
            if getattr(agent, "_memory_enabled", True):
                memory_block = store.format_for_system_prompt("memory") or ""
            if getattr(agent, "_user_profile_enabled", True):
                user_block = store.format_for_system_prompt("user") or ""
        except Exception:
            pass

    # Tool-schema JSON — the other half of the fixed per-call payload.
    tools = getattr(agent, "tools", None) or []
    tools_json = json.dumps(tools, ensure_ascii=False)

    sections: List[Tuple[str, int, int]] = [
        ("stable (identity/guidance/skills)", len(stable), _bytes(stable)),
        ("context (AGENTS.md/cwd files)", len(context), _bytes(context)),
        ("volatile (memory/profile/timestamp)", len(volatile), _bytes(volatile)),
    ]

    # 固定前缀预算判定（与每轮开始那一行警告同一函数、同一判据）。
    from agent.prompt_builder import prompt_budget_verdict

    try:
        from agent.model_metadata import get_model_context_length

        window = get_model_context_length(
            agent.model or "",
            base_url=getattr(agent, "base_url", ""),
            api_key=getattr(agent, "api_key", ""),
            provider=getattr(agent, "provider", ""),
        )
    except Exception:
        window = None

    budget = prompt_budget_verdict(
        full,
        tools_json,
        context_length=window,
        sections=[
            ("stable tier (身份/指引/技能索引)", stable),
            ("context tier (AGENTS.md 与工作目录文件)", context),
            ("volatile tier (记忆/画像/时间戳)", volatile),
            ("tool schemas", tools_json),
        ],
    )

    return {
        "platform": platform,
        "model": getattr(agent, "model", "") or "",
        "system_prompt": {"chars": len(full), "bytes": _bytes(full)},
        "skills_index": {"chars": len(skills_index), "bytes": _bytes(skills_index)},
        "memory": {"chars": len(memory_block), "bytes": _bytes(memory_block)},
        "user_profile": {"chars": len(user_block), "bytes": _bytes(user_block)},
        "tools": {"count": len(tools), "json_bytes": _bytes(tools_json)},
        "sections": sections,
        "budget": budget,
    }


def _fmt_kb(n: int) -> str:
    return f"{n / 1024:.1f} KB"


def render_breakdown(data: Dict[str, Any]) -> str:
    """Render the breakdown as plain text suitable for a terminal."""
    lines: List[str] = []
    sp = data["system_prompt"]
    lines.append(f"Prompt-size breakdown (platform={data['platform']}, model={data['model'] or 'unset'})")
    lines.append("")
    lines.append(f"  System prompt total : {sp['bytes']:>8,} B  ({_fmt_kb(sp['bytes'])}, {sp['chars']:,} chars)")
    lines.append("")
    lines.append("  Major blocks:")
    si = data["skills_index"]
    mem = data["memory"]
    up = data["user_profile"]
    lines.append(f"    skills index       : {si['bytes']:>8,} B  ({_fmt_kb(si['bytes'])})")
    lines.append(f"    memory             : {mem['bytes']:>8,} B  ({_fmt_kb(mem['bytes'])})")
    lines.append(f"    user profile       : {up['bytes']:>8,} B  ({_fmt_kb(up['bytes'])})")
    lines.append("")
    lines.append("  Prompt tiers:")
    for label, chars, byts in data["sections"]:
        lines.append(f"    {label:<36}: {byts:>8,} B  ({_fmt_kb(byts)})")
    lines.append("")
    tools = data["tools"]
    lines.append(f"  Tool schemas         : {tools['json_bytes']:>8,} B  ({_fmt_kb(tools['json_bytes'])}, {tools['count']} tools)")
    lines.append("")
    b = data.get("budget") or {}
    if b:
        est = "窗口未知，只按地板 12,000" if not b.get("context_length") else (
            f"窗口 {b['context_length']:,} × {int(b['window_fraction'] * 100)}%"
        )
        flag = "超" if b.get("over") else "在预算内"
        lines.append(
            f"  Per-call prefix      : {b['used_tokens']:,} tokens "
            f"(system {b['system_prompt_tokens']:,} + tools {b['tools_tokens']:,}) "
            f"— {flag}，上限 {b['limit_tokens']:,} ({est})"
        )
        lines.append(
            "                         token 数是仓里既有粗估（约 4 字符/token），"
            "中文比英文密，所以这行是**下界**：真实值可能到 1.5–2 倍。"
        )
        if b.get("biggest", {}).get("label"):
            lines.append(
                f"  Biggest block        : {b['biggest']['label']} — {b['biggest']['tokens']:,} tokens"
            )
        if b.get("over"):
            lines.append(
                "                         这一份是**每一轮都付**的固定成本。要收就从上面那块收；"
                "确要放宽写 config 的 prompt_max_tokens。"
            )
    return "\n".join(lines)


def cmd_prompt_size(args: Any) -> None:
    """Entry point for ``plobi prompt-size``."""
    platform = getattr(args, "platform", "cli") or "cli"
    as_json = getattr(args, "json", False)
    try:
        data = compute_prompt_breakdown(platform)
    except Exception as e:
        print(f"Could not compute prompt-size breakdown: {e}")
        return
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(render_breakdown(data))
