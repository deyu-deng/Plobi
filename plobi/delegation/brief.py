"""派活四要素（``WP-OFFLOAD-BRIEF``，11-22）。

依据 Anthropic 多智能体那套写法：交给别人的活要写清四件事，少一件它就会在路上
停下来问你，或者回来给你一个你用不上的东西。

    目标    做到什么程度算完
    产出    要什么形状的东西（文件/一段文本/一张表），落在哪儿
    工具来源 它可以从哪儿拿工具、命令、资料
    边界    不许做什么（改别的文件、装东西、动远端、花钱）

**缺了不拒绝**：一句「把 README 补个用法」本来就不必有边界。但缺哪样要当场说出来，
因为缺边界的活十有八九会停下来等一句授权——那本来该在派活时说完。
"""

from __future__ import annotations

from typing import Iterable

#: 四要素的键与中文名。顺序就是拼进正文的顺序，也是提示的顺序。
FIELDS: tuple[tuple[str, str], ...] = (
    ("goal", "目标"),
    ("deliverable", "产出"),
    ("tools", "可用"),
    ("bounds", "边界"),
)

#: 缺哪样，代价是什么。提示只讲代价，不讲规矩——规矩没人看。
_GAP_COST = {
    "deliverable": "没说清要什么样的产出，回来的东西可能对不上你要的形状",
    "tools": "没说它可以用什么，它可能因为没工具而绕远路",
    "bounds": "没写边界，它多半会停下来问你一句授权——那句本该现在就说",
}


def check_brief(goal: str, deliverable: str = "", tools: str = "", bounds: str = "") -> list[str]:
    """缺哪几样（返回键，按 :data:`FIELDS` 顺序）。全齐则空列表。"""
    given = {"goal": goal, "deliverable": deliverable, "tools": tools, "bounds": bounds}
    return [key for key, _ in FIELDS if not str(given.get(key) or "").strip()]


def gaps_hint(gaps: Iterable[str]) -> str:
    """把缺项读成人话（一句话，逗号连起来）。"""
    pains = [_GAP_COST[key] for key in gaps if key in _GAP_COST]
    return "；".join(pains)


def compose_brief(goal: str, deliverable: str = "", tools: str = "", bounds: str = "") -> str:
    """把四要素拼成一段交给客席的正文。

    只拼**给了的**那几样：没给的不留空标题，免得它照着空标题自己编一段边界出来。
    """
    text = str(goal or "").strip()
    parts = [f"【目标】{text}" if text else ""]
    for key, label in FIELDS[1:]:
        value = str({"deliverable": deliverable, "tools": tools, "bounds": bounds}[key] or "").strip()
        if value:
            parts.append(f"【{label}】{value}")
    return "\n".join(part for part in parts if part)
