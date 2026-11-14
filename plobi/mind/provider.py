"""Mind memory provider — Plobi ↔ Mind second-brain surface adapter.

============================================================================
STATUS: IMPLEMENTED (P0).
============================================================================
This file wires ``MindProvider`` to the ``MemoryProvider`` ABC so the plugin
is discoverable, loadable, and activatable by Plobi. P0 implements the real
disk I/O for every lifecycle method while respecting the compliance boundary
below. All writes funnel through :class:`plobi.mind.writer.MindWriter`
(serialized + verifier + commit switch) and are pre-checked with ``_is_safe``.

Full spec + evidence:  Docs/specs/memory-adapter.md  (repo root)
Mind repo (official):  ``mind/`` (lowercase, inside the repository;
                       env ``MIND_ROOT`` overrides — never hardcode drive letters)
Mind verifier:         Mind/scripts/verifier.py (inside the Mind repo)

Why core and not a plugin?
  裁定 42.3／用户 2026-10-25「不要再是插件形态了」：本提供者现在是内核的官方记忆面
  （``agent/memory_manager.py::OFFICIAL_MEMORY_PROVIDER``），``plugins/memory/mind/`` 已退役。
  ``load_memory_provider`` 用 ``CORE_PROVIDERS`` 短路解析 ``mind``，配置项不变。

Compliance boundary (CRITICAL — see plan §3/§5):
  Mind's git pre-commit verifier BLOCKS commits where:
    (1) Dossiers/projects top-level dir names != AGENTS.md §1 declaration, OR
    (2) （技能已不在脑仓：2026-10-25 迁到 $PLOBI_HOME/skills，本条对脑不再适用）
  **记忆**的读写只在 ``MEMORY_ZONES``（脑里的 ``Mind/wiki/*``）内——人读面 ``Notes/``／``Diary/``…
  不属于记忆系统（用户 10-24「surface 不要进记忆系统」、10-25「算越界」）。宽的那张
  ``SAFE_PREFIXES`` 只服务管道写入（课程笔记、名册登记），别把两者当同一张表。
  Writing outside either risks BLOCKING Mind's commits or polluting the brain.
"""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.memory_provider import MemoryProvider
from plobi.mind.paths import (
    MEMORY_ZONES,
    SAFE_PREFIXES,
    UnsafeMindPath,
    is_memory_zone,
    logical_target,
    normalize_logical,
    real_target,
    safe_target,
)
from plobi.mind.writer import MindWriter

logger = logging.getLogger(__name__)

# --- Compliance boundary -------------------------------------------------
# Real writes MUST resolve under one of these relative prefixes (relative to
# MIND_ROOT). These are the zones Mind's verifier does NOT block. Do not add
# Dossiers/projects/<new> or $PLOBI_HOME/skills/<new> here — those require editing
# own AGENTS.md declarations, which this plugin must never do automatically.
# NOTE: the Plobi project zone is the official capitalised ``Plobi`` dir —
# the lowercase ``plobi`` dir is a legacy Plobi archive and must never be
# created by accident (writing there would pollute the knowledge base).
# SAFE_PREFIXES is imported from plobi.mind.paths — the single source of
# truth. Do NOT redefine it here; updating one without the other causes
# security checks to diverge.

MIND_READ_TOOL = "mind_read"
MIND_WRITE_TOOL = "mind_write"

# 脑那一面的「门类键」——它不是逻辑前缀，只是提示表里的一格。
MIND_FACE = "Mind"

VAULT_READ_MAX_CHARS = 24_000
VAULT_WRITE_MAX_CHARS = 60_000
# 盘上没有 ``Mind`` 这一层——脑仓摊平之后顶层就是 ``wiki/ raw/ persona/``（现核）。
# ``Mind/`` 是**内部与存量**的旧拼法（待批队列、信任账、脑仓自己那份 AGENTS.md 里都写着它），
# 所以给人和模型看的说法用 ``Mind/``，进 paths 之前在这一格归一；把 ``Mind/`` 全线改名是另一把刀
# （登记在 `Docs/reference/agent-register.md`，要连 writer／CLI／脑仓那份文件一起动）。
BRAIN_SPOKEN_PREFIX = "Mind/"
BRAIN_LOGICAL_PREFIX = "Mind/"


def _to_logical(path: str) -> str:
    """模型说的路径 → 内部用的逻辑路径。

    一处归一：``Mind/…`` 归成内部的 ``Mind/…``。``normalize_logical`` 只管斜杠与首尾，
    ``Surface/notes/…`` 那套旧写法 2026-11-13 已删（不再翻案）。
    """
    text = str(path or "").replace("\\", "/").strip("/")
    if text.startswith(BRAIN_SPOKEN_PREFIX):
        text = BRAIN_LOGICAL_PREFIX + text[len(BRAIN_SPOKEN_PREFIX) :]
    return normalize_logical(text)


def _to_spoken(path: str) -> str:
    """反向：回给模型的字符串里不许出现盘上没有的那个词。"""
    if path.startswith(BRAIN_LOGICAL_PREFIX):
        return BRAIN_SPOKEN_PREFIX + path[len(BRAIN_LOGICAL_PREFIX) :]
    return path

# 每把工具越界时的那句提示（说清「该用哪一把」）。
#
# 只剩脑那一面一格：人读面那三类的提示表随那 17 把工具一起搬去了
# ``tools/human_common.py``（WP-HUMAN-REGISTRY-TOOLS）。
_FACE_HINTS = {
    MIND_FACE: "脑那一面用 `mind_read`／`mind_write`",
}


MAX_DIGEST_TURNS = 40
MAX_DIGEST_CHARS_PER_MSG = 1200

# WP-MIND 读接缝：画像/档案状态窄切进上下文。
#
# 限量口径（11-09，数值都在这里，可调）：
# * **按类目各自限量**：项目 3 份、研究 2 份。不是拿一个总数去切——项目一多，
#   研究那两类就一份都进不来（真数据上 research 就 SRTP／Wealth-Lab 两份）。
# * **当前那一份无条件入选**（它占掉一个名额，类内其余按名字补足）：旗舰项目
#   不许被字母序切掉（以前按目录名截前 3，``Plobi`` 自己那个项目被切掉了）。
# * 单档的宽度：目标 200 字 ＋ 现状 320 字；整块封顶 2800 字（窄切不淹上下文）。
PROFILE_PROJECT_LIMIT = 3
PROFILE_RESEARCH_LIMIT = 2
PROFILE_GOAL_MAX_CHARS = 200
PROFILE_STATUS_MAX_CHARS = 320
PROFILE_PERSONA_MAX_CHARS = 800
PROFILE_MAX_TOTAL_CHARS = 2800
# 阶段那一行（**目标 ＋ 现状 ＋ 阶段**三样都是屏上那张卡有的东西），
# 宽度按「几个节点 ＋ 正在做那个的标题」算，不把每个节点的 detail 灌进来。
PROFILE_STAGE_MAX_CHARS = 120


def _clip(text: str, limit: int) -> str:
    """截到上限，截了要看得出来（尾上那点省略号）。"""
    flat = (text or "").strip()
    if len(flat) <= limit:
        return flat
    return flat[: max(0, limit - 1)].rstrip() + "…"


def _stage_line(brief) -> str:
    """阶段那一行：`阶段：2/4 已完成 · 在做 <标题>`。

    压成一行而不是逐条列：阶段条在屏上是一排节点，灌进上下文的是**走到哪儿了**，
    不是每个节点的 detail（那要靠 ``dossier_write`` 去看档案本体）。
    """
    stages = getattr(brief, "stages", None) or []
    if not stages:
        return ""
    total = len(stages)
    done = getattr(brief, "stages_done", 0)
    current = getattr(brief, "current_stage", None)
    piece = f"阶段：{done}/{total} 已完成"
    if current is not None:
        title = (getattr(current, "title", "") or "").strip()
        if title:
            piece += " · 在做 " + _clip(title, PROFILE_STAGE_MAX_CHARS)
    return piece


def _profile_block(root: Path, *, include_persona: bool = True, current: str = "") -> str:
    """档案状态的窄切读取（无模型、纯文件）。

    来源：``<MIND_ROOT>/persona/profile.md``（详版画像；用户 10-24：画像不是 skill，
    是脑的一部分）＋ ``Dossiers/{projects,research}/<档案>/dossier.json``
    （**档案本体**：目标与现状）。

    **屏上与上下文同一份数据**：屏上那张卡显示的现状（``agent_dossier`` 的 ``summary``
    那一格）与这里那一段现状读的是同一个 ``dossier.json`` 的 ``status``——不许屏上一套、
    上下文另一套（两个口径迟早分叉，分叉了模型就在拿一份假现状做判断）。

    **目标与现状都进**（目标在前、现状在后，各带标签）：以前这一格写的是
    ``brief.status or brief.goal``，而 ``status`` 几乎从不为空 ⇒ 目标那一格永远
    进不来（模型只看得见"现在怎么样"，看不见"要什么"）。**阶段也进**（压成一行
    「几个节点、几个做完了、正在做哪个」）——屏上那张卡有阶段条，模型也该看见
    走到哪儿了；三样（目标／现状／阶段）都出自同一份 ``dossier.json``。

    ``include_persona=False`` 时只取档案段、跳过用户画像段：画像已由稳定记忆段
    USER PROFILE 承载，两处同时注入构成同源双份（L1 注入审计 P4，2533 字符重叠）。
    """
    from plobi.mind.reader import CATEGORY_LABELS, MindReader

    ctx = MindReader(root).context(
        project_limit=PROFILE_PROJECT_LIMIT,
        research_limit=PROFILE_RESEARCH_LIMIT,
        current=current,
    )
    if ctx.is_empty:
        return ""

    sections: List[str] = []
    if include_persona and ctx.persona:
        sections.append(f"### 用户画像\n{_clip(ctx.persona, PROFILE_PERSONA_MAX_CHARS)}")
    for brief in ctx.dossiers:
        label = CATEGORY_LABELS.get(brief.category, brief.category)
        lines: List[str] = []
        if brief.goal:
            lines.append("目标：" + _clip(brief.goal, PROFILE_GOAL_MAX_CHARS))
        if brief.status:
            lines.append("现状：" + _clip(brief.status, PROFILE_STATUS_MAX_CHARS))
        # **阶段**也进（屏上那张卡有阶段条，模型也该看见走到哪儿了）——
        # 压成一行「几个节点、几个做完了、正在做哪个」，不把每个节点的 detail 灌进来。
        stage_line = _stage_line(brief)
        if stage_line:
            lines.append(stage_line)
        if not lines:
            continue
        sections.append(f"### {label} {brief.name}\n" + "\n".join(lines))
    if not sections:
        return ""

    # 总量封顶：装不下就整段不再往下加（当前那一份在第一段，永远不会被砍掉）。
    head = "[Mind 画像]\n"
    body = ""
    for section in sections:
        candidate = f"{body}\n\n{section}" if body else section
        if body and len(head) + len(candidate) > PROFILE_MAX_TOTAL_CHARS:
            break
        body = candidate
    if not body:
        return ""
    return head + body


def _resolve_root() -> Path:
    """Resolve the Mind root.

    Delegates to :mod:`plobi.mind.paths` so there is one resolver and no
    hardcoded drive letters (the North Star contract requires ``MIND_ROOT``).
    Returns a non-existent placeholder when nothing is configured, so callers
    can keep using ``.is_dir()`` as the availability check.
    """
    from plobi.mind.paths import resolve_root as _shared_resolve

    root = _shared_resolve()
    if root is not None:
        return root
    # Absolute and non-existent: every caller gates on ``.is_dir()``, and a
    # relative placeholder would answer against whatever cwd the host has.
    return Path(tempfile.gettempdir()) / "plobi-mind-root-not-configured"


def _is_safe(target: Path, root: Path) -> bool:
    """Return True if ``target`` lies inside SAFE_PREFIXES.

    Call this BEFORE every real write; reject + ``logger.warning`` otherwise.
    The comparison goes through :func:`logical_target` because a split layout puts
    the human-facing zones outside ``root`` — subtracting the Mind root from such a
    path raises, which used to read as "outside the safe prefixes" and would have
    silently refused every legitimate note write after the move.
    """
    return logical_target(target, root) is not None


def _kebab(text: str) -> str:
    """Normalise an arbitrary string to kebab-case (Mind convention)."""
    slug = re.sub(r"[^A-Za-z0-9\u4e00-\u9fff]+", "-", text.strip()).strip("-").lower()
    return re.sub(r"-{2,}", "-", slug)


def _render_digest(messages: List[Dict[str, Any]]) -> str:
    """Render a bounded session digest from the OpenAI-style message list."""
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines: List[str] = [f"# Session Digest — {stamp}", ""]
    count = 0
    for msg in messages or []:
        role = msg.get("role", "")
        content = msg.get("content")
        if role not in ("user", "assistant") or not isinstance(content, str) or not content.strip():
            continue
        text = content.strip()
        if len(text) > MAX_DIGEST_CHARS_PER_MSG:
            text = text[:MAX_DIGEST_CHARS_PER_MSG].rstrip() + "\n…"
        lines.append(f"## {role}")
        lines.append(text)
        lines.append("")
        count += 1
        if count >= MAX_DIGEST_TURNS:
            lines.append("… (truncated)")
            break
    return "\n".join(lines)


# ── 改 frontmatter 的那几格（正文一个字不动） ────────────────────────────────
#
# 这几格怎么写，**格式真相在 :mod:`plobi.human.note_manage`**（frontmatter 的切法、
# 标量的引号、行尾那句 ``# 备注`` 怎么办，都在那儿）——这里只借用它那几个小工具，
# 不另开一份格式口径。









class MindProvider(MemoryProvider):
    """Plobi memory provider that bridges to the Mind surface.

    内核模块（裁定 198：官方脑不是插件形态）——`plugins/memory/mind/` 只剩注册壳与 CLI。
    """

    def __init__(self) -> None:
        self._session_id: str = ""
        self._mind_root: Optional[Path] = None
        self._prefetch_cache: Dict[str, str] = {}
        self._agent_context: str = "primary"
        # **当前是谁**：会话身份（``agent/agent_init.py`` 传进来的 ``agent_identity``）。
        # 上下文里"当前那一份档案优先"就靠它——名册上的名字与盘上的目录名可能只差大小写。
        self._agent_identity: str = ""
        self._agenda_published_date: str = ""

    @property
    def name(self) -> str:
        return "mind"

    # -- required abstract methods ---------------------------------------

    def is_available(self) -> bool:
        """Report availability if MIND_ROOT resolves to a real surface."""
        return _resolve_root().is_dir()

    def initialize(self, session_id: str, **kwargs) -> None:
        """Resolve root + session. Builds a writer handle lazily on write."""
        self._session_id = session_id
        self._mind_root = _resolve_root()
        # cron / flush 等非 primary 上下文不得往 Mind 写运行时摘要。
        self._agent_context = str(kwargs.get("agent_context") or "primary")
        # 会话身份（每个 Agent 一份）：只用来决定"上下文里当前那一份档案是谁"。
        self._agent_identity = str(kwargs.get("agent_identity") or "").strip()
        logger.info(
            "[mind] initialized for session %s (root=%s, context=%s, identity=%s)",
            session_id, self._mind_root, self._agent_context, self._agent_identity or "-",
        )

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        """**两把**原子工具，每把**只碰脑那一面**（用户 11-10 拍板「原子工具按种类拆」）。

        人读面那三样各有各的契约——笔记有 frontmatter 硬约束、日记永远先问、档案是
        字段级结构化记录——所以拆成 ``note_*``／``diary_*``／``dossier_*``，
        脑这一面的契约只有一种 ⇒ 仍旧一对 ``mind_read``／``mind_write``。

        **那 17 把人读面的工具已经搬出 provider**（WP-HUMAN-REGISTRY-TOOLS，11-14）：
        它们现在是**注册表工具**（``tools/human_note_tools.py``／
        ``tools/human_diary_tools.py``／``tools/human_dossier_tools.py``），归
        ``note``／``diary``／``dossier`` 三个门类 ⇒ **provider 只管脑页**，而人读面
        不再跟着「换个记忆服务就一起没了」。

        写文件的那几把一律走 :class:`MindWriter`，因此过 L1 那道闸：这台
        ``mind_write_auto_approve`` 关着时落不了盘，就照实回「已进待批 + id」。
        """
        return [
            {
                "name": MIND_READ_TOOL,
                "description": (
                    "Read one page of the brain's OWN surface by its LOGICAL path, "
                    "``Mind/wiki/...`` — concept pages, entity pages, source pages, "
                    "comparisons, the daily summary, memory notes. This is the AI-facing "
                    "surface: what is written here is mostly machine product, not prose for "
                    "the user. Only paths inside those zones resolve; the human side "
                    "(notes, the diary, dossiers) belongs to other tools."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Logical path on the brain surface, e.g. Mind/wiki/concepts/plobi-x.md",
                        }
                    },
                    "required": ["path"],
                },
            },
            {
                "name": MIND_WRITE_TOOL,
                "description": (
                    "Overwrite one page on the brain's OWN surface (``Mind/wiki/...``) by its "
                    "LOGICAL path — the zones this tool may touch are the memory system's own "
                    "pages (concepts, entities, sources, comparisons, daily, memory). Keep the "
                    "file's frontmatter. Notes for the user, the diary and dossiers "
                    "belong to other tools. The write is "
                    "checked by the trust gate; the result says whether it landed, went to "
                    "the pending queue (with an id), or was refused, and why. Never report "
                    "success the model did not read back."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Logical path on the brain surface, e.g. Mind/wiki/memory/x.md",
                        },
                        "content": {
                            "type": "string",
                            "description": "The full new contents of that page (markdown).",
                        },
                    },
                    "required": ["path", "content"],
                },
            },
        ]

    def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs) -> str:
        """Serve the brain's own tools; anything else is an explicit no-op.

        **只有脑那一面**：``mind_read``／``mind_write``。
        人读面那 17 把是**注册表工具**（走 ``tools/registry.py``），不在这里
        （WP-HUMAN-REGISTRY-TOOLS，11-14）。
        """
        if tool_name == MIND_READ_TOOL:
            return json.dumps(
                self._tool_read(args, MEMORY_ZONES, _FACE_HINTS[MIND_FACE]), ensure_ascii=False
            )
        if tool_name == MIND_WRITE_TOOL:
            return json.dumps(
                self._tool_write(args, MEMORY_ZONES, _FACE_HINTS[MIND_FACE]), ensure_ascii=False
            )
        return json.dumps(
            {"success": False, "error": f"mind provider does not handle tool {tool_name!r}"},
            ensure_ascii=False,
        )

    @staticmethod
    def _tool_face_guard(logical: str, allowed: tuple[str, ...], hint: str) -> Dict[str, Any] | None:
        """一枚工具只碰一类：越类的形状当场说清，不装作读不到。

        ``plobi/mind/paths.py`` 的白名单比这几类加起来还宽（管道自己写脑的日页面要过它），
        所以这里不是重复检查，是**模型的手**与**管道的写路**各自的面。
        """
        head = logical.strip("/").partition("/")[0]
        if any(head == a or logical == a or logical.startswith(a + "/") for a in allowed):
            return None
        return {
            "success": False,
            "error": (
                f"{_to_spoken(logical)} 不在这一枚工具的面里。能碰的形状只有："
                + "、".join(f"`{_to_spoken(a)}`" for a in allowed)
                + f"——{hint}。"
            ),
        }


    def _tool_read(self, args: Dict[str, Any], allowed: tuple[str, ...], face: str = "") -> Dict[str, Any]:
        from plobi.mind.paths import MindUnavailable, is_safe_relative, require_root

        shown = str((args or {}).get("path") or "").strip().replace("\\", "/")
        path = _to_logical(shown)
        if not path:
            return {"success": False, "error": "path is required"}
        off = self._tool_face_guard(path, allowed, face or _FACE_HINTS.get(allowed[0], ""))
        if off:
            return off
        if not is_safe_relative(path):
            return {"success": False, "error": f"{shown} 不在可读写的面里"}
        try:
            root = require_root(self._mind_root)
            target = safe_target(root, path)
        except (MindUnavailable, UnsafeMindPath) as exc:
            return {"success": False, "error": str(exc)}
        if not target.is_file():
            return {"success": False, "error": f"{shown} 还不存在（读不到不等于空文件，别当它是空的）"}
        try:
            text = target.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return {"success": False, "error": str(exc)}
        truncated = len(text) > VAULT_READ_MAX_CHARS
        return {
            "success": True,
            "path": shown,
            "content": text[:VAULT_READ_MAX_CHARS],
            "chars": len(text),
            "truncated": truncated,
        }

    def _tool_write(self, args: Dict[str, Any], allowed: tuple[str, ...], face: str = "") -> Dict[str, Any]:
        from plobi.human.notes_contract import validate_note
        from plobi.mind.paths import MindUnavailable, is_safe_relative
        from plobi.mind.writer import WriteRequest

        payload = args or {}
        shown = str(payload.get("path") or "").strip().replace("\\", "/")
        path = _to_logical(shown)
        content = payload.get("content")
        if not path:
            return {"success": False, "error": "path is required"}
        if not isinstance(content, str) or not content.strip():
            return {"success": False, "error": "content must be a non-empty string"}
        if len(content) > VAULT_WRITE_MAX_CHARS:
            return {
                "success": False,
                "error": f"content too large ({len(content)} chars > {VAULT_WRITE_MAX_CHARS})",
            }
        off = self._tool_face_guard(path, allowed, face or _FACE_HINTS.get(allowed[0], ""))
        if off:
            return off
        if not is_safe_relative(path):
            return {"success": False, "error": f"{shown} 不在可写的面里"}
        # 笔记那一格另有一道硬约束（用户 10-25 答「不合规拒写」）：缺 title/tags/belongs、
        # 一篇多个一级标题、发明新主题目录，一律拒，并把缺什么逐条说回去。
        notes_problems = validate_note(path, content)
        if notes_problems:
            return {
                "success": False,
                "path": path,
                "error": "笔记不合规，没有写入",
                "problems": notes_problems,
            }
        try:
            writer = MindWriter(self._mind_root)
            result = writer.write([WriteRequest(relative_path=path, content=content)])
        except MindUnavailable as exc:
            return {"success": False, "error": str(exc)}
        except Exception as exc:  # noqa: BLE001 - a failed write must not crash the turn
            logger.warning("[mind] write(%s) failed: %s", path, exc)
            return {"success": False, "error": str(exc)}

        if result.staged:
            return {
                "success": False,
                "staged": True,
                "pending_id": result.pending_id,
                "path": shown,
                "detail": result.detail,
            }
        if result.ok and path in result.written:
            return {
                "success": True,
                "written": [_to_spoken(w) for w in result.written],
                "path": shown,
                "detail": result.detail,
            }
        return {
            "success": False,
            "path": shown,
            "error": "这一笔没有落盘",
            "detail": result.detail or f"{shown} 被写路径的守卫拒了",
        }


    # -- recall (per-turn, injected as context) --------------------------

    def _profile_summary(self, *, include_persona: bool = True) -> str:
        """档案摘要（WP-MIND 读接缝）。任何失败都降级为告警 + 空串。

        ``include_persona=False`` 只保留档案段，画像交给 USER PROFILE 段
        （见 :func:`_profile_block`，审计 P4 防双份）。
        """
        try:
            root = _resolve_root()
            if not root.is_dir():
                return ""
            return _profile_block(
                root, include_persona=include_persona, current=self._agent_identity
            )
        except Exception as exc:  # noqa: BLE001 - recall must never crash a turn
            logger.warning("[mind] profile summary read failed (non-fatal): %s", exc)
            return ""

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        """Recall relevant Mind snippets to inject into the system prompt.

        WP-MIND：召回结果前置一份画像/项目状态摘要（``_profile_summary``），
        帮助模型识别「在为谁服务、项目进行到哪」。该读取是窄切（persona +
        最多 3 个项目简报，每段截断），只读、失败降级为告警。

        Checks the warm-up cache first (filled by ``queue_prefetch``), then
        falls back to a live keyword search over SAFE_PREFIXES. Returns an
        empty string when nothing relevant is found.
        """
        parts: List[str] = []
        # 审计 P4：画像段与稳定记忆 USER PROFILE 同源双份，prefetch 只补项目简报。
        profile = self._profile_summary(include_persona=False)
        if profile:
            parts.append(profile)
            logger.info(
                "[mind] prefetch includes project briefs, persona deferred to USER PROFILE (%d chars)",
                len(profile),
            )

        root = _resolve_root()
        if not root.is_dir() or not query:
            return "\n\n".join(parts)

        cached = self._prefetch_cache.pop(query, None)
        if cached is not None:
            parts.append(cached)
            return "\n\n".join(parts)

        try:
            from .retrieval import search as _mind_search

            snippets = _mind_search(root, query, limit=5)
        except Exception as exc:  # noqa: BLE001 - recall must never crash a turn
            logger.warning("[mind] prefetch failed (non-fatal): %s", exc)
            return "\n\n".join(parts)
        parts.extend(snippets)
        return "\n\n".join(parts)

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        """Queue background recall for the NEXT turn.

        Lightweight synchronous warm-up: runs the keyword search now and
        caches the formatted result so the next ``prefetch(query)`` hits the
        cache instead of scanning the surface again.
        """
        if not query:
            return
        try:
            root = _resolve_root()
            if not root.is_dir():
                return
            from .retrieval import search as _mind_search

            snippets = _mind_search(root, query, limit=5)
            if snippets:
                self._prefetch_cache[query] = "\n\n".join(snippets)
        except Exception as exc:  # noqa: BLE001 - background, never fatal
            logger.warning("[mind] queue_prefetch failed (non-fatal): %s", exc)

    # -- persist (per-turn) ----------------------------------------------

    def _maybe_publish_daily_agenda(self) -> None:
        """WP-MIND 写接缝：每会话每天一次，把当日日程摘要发进 Mind。

        走既有 ``plobi.agenda.mind_sync`` 通道（MindWriter 串行服务、无模型
        渲染、目标 ``Mind/wiki/daily/<date>.md``（surface 不进记忆系统））。不新增 API、
        不并行写。任何失败只降级为日志告警，绝不阻塞主对话流；非 primary
        上下文（cron/flush）直接跳过，运行时状态真源始终是 SQLite（ADR-0007）。
        """
        if self._agent_context != "primary":
            return
        today = datetime.now().strftime("%Y-%m-%d")
        if self._agenda_published_date == today:
            return
        self._agenda_published_date = today  # 当天不再重试，避免失败刷屏/每轮重写
        try:
            from plobi.agenda.mind_sync import publish_project_daily_summary

            result = publish_project_daily_summary()
            if result.ok:
                logger.info(
                    "[mind] daily agenda summary published to Mind/wiki/daily"
                )
            else:
                logger.warning(
                    "[mind] daily agenda summary not written: %s", result.detail
                )
        except Exception as exc:  # noqa: BLE001 - persistence is non-fatal
            logger.warning("[mind] daily agenda summary failed (non-fatal): %s", exc)

    def sync_turn(
        self,
        user_content: str,
        assistant_content: str,
        *,
        session_id: str = "",
        messages: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        """每轮**不再**往脑里落第二份聊天原文（裁定 198，用户 10-23 10:0x）。

        旧腿是 ``Mind/raw/chat-logs/exports/<会话>.md`` 逐轮 append——那是旧 Mind 形态
        （单层 ``Mind/`` ＋ Mac↔Win 靠 git 传原文）的处理区。现在会话的正本是单库
        ``state.db``（裁定 180 行级归属），再抄一份进**带远端**的脑仓，等于把裁定 157
        那句「聊天原文不上云」每天往前推一次。现核：那批文件的写者只有这里，
        **读侧一个都没有**（``retrieval.py`` 不扫 ``raw/``），所以关掉这条腿不丢任何检索能力。

        **每日日程摘要那条自动腿已停**（用户 2026-10-29 原话：「每日摘要是干什么用的。
        我都没有定义过。是你自己说的东西。」）——它每轮想写一份 `wiki/daily/<日期>.md`，
        但那份计划从来没被点过头，于是每开一个新进程/新一天就再排一张，待确认层里堆到 22 张、
        21 张过期。没有人要它，就不该拿它去问人。函数 `_maybe_publish_daily_agenda` 留着，
        要跑得由人显式叫；`AgendaService.daily_summary` 的确定性渲染本身没动。
        """
        # self._maybe_publish_daily_agenda()  # 停用，理由见上；不要再悄悄接回来

    # -- session boundary ------------------------------------------------

    def on_session_end(self, messages: List[Dict[str, Any]]) -> None:
        """会话收尾不再往脑的原料区写 digest（裁定 198）；这里是**有意的空操作**。

        旧腿写 ``Mind/raw/chat-logs/digested/<日期>/<会话>-digest.md``。它和 turn note 同一个病：
        正本在 ``state.db``，脑仓不该收第二份拷贝，而且现核也没有任何读侧。
        digest 这件事本身没有作废——它的归宿是 L1 那道闸（计划＋sha256＋你点头）之后进正典，
        归 ``WP-MEMORY-LAYERING``。:func:`_render_digest` 因此保留给那一刀，别当死代码删掉。
        """
        return None

    # -- mirror built-in memory writes -----------------------------------

    def on_memory_write(
        self,
        action: str,
        target: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Mirror Plobi MEMORY.md/USER.md writes into the brain (L2 canon).

        - ``remove`` actions never delete from the second brain (log only).
        - concept/entity/source-kind metadata routes to ``Mind/wiki/concepts/``
          (``plobi-<slug>.md``); everything else lands in
          ``Mind/wiki/memory/<slug>.md``. surface 不进记忆系统（用户 10-24）。
        - 双重检查：既要落在**记忆区**（:func:`is_memory_zone` ⇒ ``MEMORY_ZONES``，
          前缀表里属于脑的那半），也要过管道闸 ``_is_safe``。所以一条指向
          ``Notes/`` 那类的记忆写入会被拒——尽管管道服务确实有正当理由往那儿写课程笔记。

        Triggered by MemoryManager.notify_memory_tool_write (verified caller
        at tool_executor.py:1301 / agent_runtime_helpers.py:2298).
        """
        try:
            root = _resolve_root()
            if not root.is_dir():
                return
            if action == "remove":
                logger.info("[mind] on_memory_write 'remove' ignored (never delete from Mind): %s", target)
                return
            if not content:
                return

            slug = _kebab(Path(str(target)).stem) or _kebab(target) or "memory-note"
            meta = metadata or {}
            kind = str(meta.get("kind", "")).lower()
            if kind in ("concept", "entity", "source") or "concepts" in str(target).lower():
                rel = f"Mind/wiki/concepts/plobi-{slug}.md"
                page_type = kind if kind in ("concept", "entity", "source") else "concept"
            else:
                rel = f"Mind/wiki/memory/{slug}.md"
                page_type = "synthesis"

            if not is_memory_zone(rel) or not _is_safe(real_target(rel, root), root):
                logger.warning("[mind] refused on_memory_write (outside MEMORY_ZONES): %s", rel)
                return

            # 正典页要带词表里合法的键，否则落盘前会被 MindWriter 的闸拒掉
            # （plobi/mind/schema.py 与脑侧 verifier 读同一份 wiki/meta/schema.json）。
            # status 一律 unreviewed：这条镜像是 AI 写的，没人复核过就不假装复核过。
            body = (
                "---\n"
                f"type: {page_type}\n"
                "status: unreviewed\n"
                f"tags: [plobi-memory]\n"
                f"title: plobi-{slug}\n"
                "---\n\n"
                f"# {slug}\n\n{content.strip()}\n"
            )
            result = MindWriter(root, run_verifier=True, commit=False).write_one(
                rel, body, mode="overwrite"
            )
            if not result.ok:
                logger.warning("[mind] on_memory_write failed: %s", result.detail)
        except Exception as exc:  # noqa: BLE001 - mirroring is non-fatal
            logger.warning("[mind] on_memory_write failed (non-fatal): %s", exc)

    # -- backup integration ---------------------------------------------

    def backup_paths(self) -> List[str]:
        """Declare Mind root so `plobi backup` includes it (outside PLOBI_HOME)."""
        root = _resolve_root()
        return [str(root)] if root.is_dir() else []
