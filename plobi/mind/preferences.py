"""从评论长出偏好条目（规格 ``specs/memory-consent-learning.md`` §3.2，P1）。

三层范式里的第二层：**评论不是附注，是规则草案**。这一格只做两件事——
把评论变成可读的候选、把候选写成一张**等人点头**的计划；它不替人点头。

四条硬规矩（规格 §3.2／§5.2／§8 第 5 条）：

1. **候选不自动生效**：唯一的落盘路径是 ``MindWriter(require_approval=True)``，
   产出是一张待批纸；人点头之后才变成正典里的字（沿用裁定 199 的 L1 闸）。
2. **正典是 markdown**：``Mind/wiki/memory/preferences.md``（``MEMORY_ZONES`` 内，
   在 ``plobi/mind/paths.py:70`` 那一格），人可读可改可删——改这一行字就是改规则本身。
3. **不进每轮召回**：这个文件不塞进 prefetch；写计划那一步按 ``category`` 命中注入
   （用户 11-16 21:4x 拍定「单文件，不进每轮」）。
4. **一条评论 ≤ 一条候选**：先去硬限，跑出重复再谈合并（规格 §8.1 仍在未决那一格）。

抽取器**可插拔**：默认是 ``verbatim``——把用户原话归一成一行，**零模型调用**，
不替用户改写成"更漂亮的规则"。将来的 L2 便宜模型从这里接（``extract=`` 传一个
callable），接上之后仍然只是「起草」，不改变「人点头才生效」。

幂等靠证据 id：每个条目下面埋一行 ``<!-- ev:<事件 id> -->``，同一条事件不会被写第二次；
所以「删掉 ``consent.db`` 重建」不会动正典里的字（规格 §5.2 那条测试钉的就是这个）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence

#: 偏好正典的逻辑路径（``MEMORY_ZONES`` 内）。逻辑头只有"种类"一种写法。
PREFERENCES_LOGICAL = "Mind/wiki/memory/preferences.md"

_HEADER = """# 记忆写入偏好

> 每一条都从你的评论里长出来，**你点头之后才生效**；改这一行字就是改规则本身，
> 删掉一条＝撤销。条目下面那行 `<!-- ev:… -->` 是它的证据（那一次表态的事件 id），
> 删证据行等于让这条偏好失去来路——别删。

"""

#: 原话归一：去首尾空白、把多行拍成一行。**不改写内容**。
def _verbatim(comment: str) -> str:
    return " ".join(line.strip() for line in str(comment or "").splitlines() if line.strip())


@dataclass(frozen=True)
class Candidate:
    """一条待点头的偏好候选。``evidence`` 是它来自哪一条表态事件。"""

    category: str
    text: str
    evidence: str
    action: str
    ts: float

    def block(self) -> str:
        return (
            f"- {self.text}\n"
            f"  <!-- ev:{self.evidence} category:{self.category} action:{self.action} -->\n"
        )


def candidates(
    book,
    *,
    category: str = "",
    limit: int = 200,
    extract: Optional[Callable[[str], str]] = None,
) -> List[Candidate]:
    """把带评论的表态事件摊成候选（一条评论一条，按时间正序）。

    只认**有原话**的事件：没写评论的点头／驳回说明态度，不说明「该怎么写」——
    偏好是从话里长出来的，不是从点击数里猜出来的。
    """
    shape = extract or _verbatim
    out: List[Candidate] = []
    seen: set[str] = set()
    for event in book.recent(limit, category=category):
        if not event.comment or event.id in seen:
            continue
        text = shape(event.comment).strip()
        if not text:
            continue
        seen.add(event.id)
        out.append(Candidate(
            category=event.category,
            text=text,
            evidence=event.id,
            action=event.action,
            ts=event.ts,
        ))
    out.sort(key=lambda c: (c.ts, c.evidence))
    return out


def _existing_text() -> str:
    """正典现在长什么样。读不到就当还没写过（不是错误：第一次就是空的）。"""
    try:
        from plobi.mind.paths import real_target, resolve_roots

        target = real_target(PREFERENCES_LOGICAL, resolve_roots())
        if target.is_file():
            return target.read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001 - 读不到就当空档，写的时候那道闸会再核一次
        pass
    return ""


def _already_in(text: str, evidence: str) -> bool:
    return f"<!-- ev:{evidence}" in text


def pending(
    book,
    *,
    category: str = "",
    limit: int = 200,
    existing: Optional[str] = None,
    extract: Optional[Callable[[str], str]] = None,
) -> List[Candidate]:
    """还没进正典的候选（幂等：同一条事件不会出现第二次）。"""
    text = _existing_text() if existing is None else existing
    return [c for c in candidates(book, category=category, limit=limit, extract=extract)
            if not _already_in(text, c.evidence)]


def render(existing: str, fresh: Sequence[Candidate]) -> str:
    """把新候选接在正典末尾。空档时先写抬头（抬头本身不含任何规则）。"""
    base = existing if existing.strip() else _HEADER
    if not base.endswith("\n"):
        base += "\n"
    block = "".join(c.block() for c in fresh)
    return base + ("\n" + block if base.strip() and not base.endswith("\n\n") else block)


def stage(
    book,
    *,
    category: str = "",
    limit: int = 200,
    writer=None,
    extract: Optional[Callable[[str], str]] = None,
):
    """把待点头的候选写成一张计划。**返回 ``WriteResult``；正典此刻还没变。**

    ``writer`` 默认 ``MindWriter(require_approval=True)``——先问人。调用方若传一个
    ``require_approval=False`` 的 writer，那就是"绕过闸"，只有验收脚本才该这么做。
    """
    fresh = pending(book, category=category, limit=limit, extract=extract)
    if not fresh:
        return None
    if writer is None:
        from plobi.mind.writer import MindWriter

        writer = MindWriter(require_approval=True)
    return writer.write_one(PREFERENCES_LOGICAL, render(_existing_text(), fresh))


def approve(pending_id: str) -> str:
    """点头一张偏好纸——走**同一处**批准实现（`write_approval_commands`），不另写一遍。"""
    from plobi_cli.write_approval_commands import handle_pending_subcommand

    return handle_pending_subcommand("memory", ["approve", pending_id])
