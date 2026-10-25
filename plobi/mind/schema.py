"""L2 正典的 frontmatter 词表——读脑里那一份真源，不在这里再声明一遍。

为什么需要这一格（``Docs/specs/memory-adapter.md`` §12.6「两条别抄」第 2 条／§12.7 第 1 条）：
claude-obsidian 自己的 ``page_schema.py`` 记着一起事故——一个 ``type:`` 值在四处声明，并集十二个、
交集两个，而它的 ``lint_engine`` 只检查字段在不在、从不检查值有没有意义。我们把取值真源放进
**脑里**（``Loom/wiki/meta/schema.json``，随定时推上私有远端，属于 L2 正典的一部分），
两侧读同一份：

- 脑侧 ``scripts/verifier.py`` 的 ``check_page_schema()`` —— 提交前的门（存量缺键＝WARN，值不合法＝BLOCKER）；
- 这里 —— ``MindWriter`` 落盘前的闸（新页值不合法或缺必填键＝**拒写**，不给人批垃圾）。

``schema.json`` 读不到时退到 :data:`FALLBACK`（本机那份的副本），并在日志里说明——
记忆这条腿不能因为没有词表就停掉，但也不能装作词表存在。
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

SCHEMA_LOGICAL = "Loom/wiki/meta/schema.json"

FALLBACK: Dict[str, Any] = {
    "checked_dirs": ["wiki/concepts", "wiki/entities", "wiki/sources",
                     "wiki/comparisons", "wiki/daily", "wiki/memory"],
    "skip_names": ["INDEX.md", "hot.md", "log.md", "overview.md", "README.md"],
    "page_types": ["concept", "entity", "source", "comparison", "synthesis",
                   "decision", "session", "digest"],
    "source_statuses": ["unreviewed", "active", "superseded", "rejected"],
    "claim_assessments": ["accepted", "provisional", "contested",
                          "unsupported", "deprecated"],
    "evidence_relations": ["supports", "contradicts", "context"],
    "required_by_dir": {"wiki/concepts": ["type"], "wiki/entities": ["type"],
                        "wiki/sources": ["type", "status"],
                        "wiki/comparisons": ["type"],
                        "wiki/daily": [], "wiki/memory": []},
}

_FM_RE = re.compile(r"^---\r?\n(.*?)\r?\n---", re.DOTALL)
_TOP_KEY_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*)\s*:\s*(.*)$")


def load_schema(root: Optional[Path] = None) -> Dict[str, Any]:
    """The brain's vocabulary file, or :data:`FALLBACK` with a logged reason."""
    from plobi.mind.paths import real_target

    text: Optional[str] = None
    try:
        path = real_target(SCHEMA_LOGICAL, root)
        if path.is_file():
            text = path.read_text(encoding="utf-8")
    except Exception as exc:  # noqa: BLE001 - a missing table must not stop memory
        logger.warning("mind schema: %s unreadable (%s); using fallback", SCHEMA_LOGICAL, exc)
    if not text:
        logger.info("mind schema: %s absent; using fallback vocabulary", SCHEMA_LOGICAL)
        return dict(FALLBACK)
    try:
        data = json.loads(text)
    except ValueError as exc:
        logger.warning("mind schema: %s unparseable (%s); using fallback", SCHEMA_LOGICAL, exc)
        return dict(FALLBACK)
    return data if isinstance(data, dict) else dict(FALLBACK)


def _frontmatter(content: str) -> Optional[Dict[str, str]]:
    match = _FM_RE.match(content or "")
    if not match:
        return None
    out: Dict[str, str] = {}
    for line in match.group(1).splitlines():
        key = _TOP_KEY_RE.match(line)
        if key:
            out.setdefault(key.group(1), key.group(2).strip())
    return out


def checked_dir_for(logical: str, schema: Dict[str, Any]) -> Optional[str]:
    """Which canon directory this logical path sits in (None = out of range)."""
    text = str(logical or "").replace("\\", "/")
    for rel_dir in schema.get("checked_dirs") or []:
        needle = f"/{str(rel_dir).strip('/')}/"
        if needle in f"/{text.lstrip('/')}/" or text.startswith(str(rel_dir)):
            return str(rel_dir)
    return None


def belongs_authority(roots=None) -> List[str]:
    """``belongs:`` 的取值只从人读面上真有的项目／课程目录名派生（现取，不另列清单）。"""
    from plobi.mind.paths import resolve_roots

    roots = roots if roots is not None else resolve_roots()
    names: List[str] = []
    base = getattr(roots, "vault", None)
    if not base:
        return names
    for sub in ("projects", "courses"):
        directory = Path(base) / sub
        if not directory.is_dir():
            continue
        for entry in sorted(directory.iterdir()):
            if entry.name.startswith("."):
                continue
            if entry.is_dir() or entry.suffix == ".md":
                names.append(entry.stem)
    return names


def violations(logical: str, content: str, *, root: Optional[Path] = None,
               creating: bool = True, schema: Optional[Dict[str, Any]] = None,
               authority: Optional[List[str]] = None) -> List[str]:
    """Why this page must not land in the canon (empty list = it may).

    值不合法永远拒；**缺必填键只在新建页时拒**（``creating=True``）——往已有页 append
    一段话不该被头上那个人类当年没写的键挡住。
    """
    schema = schema or load_schema(root)
    rel_dir = checked_dir_for(logical, schema)
    if rel_dir is None:
        return []
    name = str(logical).replace("\\", "/").rsplit("/", 1)[-1]
    if name in set(schema.get("skip_names") or []):
        return []

    meta = _frontmatter(content)
    required = required_of(schema, rel_dir)
    if meta is None:
        # 整页没有 frontmatter：只有「新建 + 那一格确实要求键」才是问题。
        # 往已有页 append 一段话时内容本来就只是片段，拿它判「没有 frontmatter」＝误伤。
        if creating and required:
            return [f"{logical}: 正典页没有 frontmatter（{rel_dir}/ 要求 {required}）"]
        return []

    reasons: List[str] = []
    if creating:
        for key in required:
            if not meta.get(key):
                reasons.append(f"{logical}: 正典页缺必填键 {key}（{rel_dir}/）")

    types = set(schema.get("page_types") or [])
    value = meta.get("type", "")
    if value and types and value not in types:
        reasons.append(f"{logical}: type「{value}」不在词表 {sorted(types)} 里")

    statuses = set(schema.get("source_statuses") or [])
    status = meta.get("status", "")
    if status and statuses and status not in statuses:
        reasons.append(f"{logical}: status「{status}」不在词表 {sorted(statuses)} 里")

    assessments = set(schema.get("claim_assessments") or [])
    assess = meta.get("claim_assessment", "")
    if assess and assessments and assess not in assessments:
        reasons.append(f"{logical}: claim_assessment「{assess}」不在词表 {sorted(assessments)} 里")

    relations = set(schema.get("evidence_relations") or [])
    if relations and "evidence" in (meta or {}):
        # 词表检查按行扫 evidence 里的 relation: ——frontmatter 里它是列表，取原文判定
        block = _FM_RE.match(content).group(1)
        for line in block.splitlines():
            m = re.search(r"relation\s*:\s*([A-Za-z_]+)", line)
            if m and m.group(1) not in relations:
                reasons.append(f"{logical}: evidence.relation「{m.group(1)}」不在词表 {sorted(relations)} 里")

    if "belongs" in meta and meta.get("belongs"):
        allowed = authority if authority is not None else belongs_authority()
        for token in re.findall(r"[^,\[\]]+", meta["belongs"]):
            name_token = token.strip().strip("'\"")
            if not name_token:
                continue
            if allowed and name_token not in allowed:
                reasons.append(
                    f"{logical}: belongs「{name_token}」指不出真源"
                    f"（人读面 projects/courses 现取名单里没有这一格）"
                )
    return reasons


def required_of(schema: Dict[str, Any], rel_dir: str) -> List[str]:
    return list((schema.get("required_by_dir") or {}).get(rel_dir) or [])
