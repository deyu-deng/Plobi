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

from typing import Iterable, Optional

#: 四要素的键与中文名。顺序就是拼进正文的顺序，也是提示的顺序。
FIELDS: tuple[tuple[str, str], ...] = (
    ("goal", "目标"),
    ("deliverable", "产出"),
    ("tools", "可用"),
    ("bounds", "边界"),
)

#: 第五段，软项：**为什么这么拆**（``WP-DISPATCH-VERDICT``，11-25）。
#:
#: 四要素说的是「要做到什么」，这一段说的是「我为什么把它切成这样、我替它定死了哪几条」。
#: Cognition 那两条原则（动作自带隐式决策；冲突决策带来坏结果）说的就是缺了这段的后果：
#: 只传结论不传决策，两个手下各自按不同假设动手，回来是两份互不相容的成品，还得你去合。
#: **不列进 :data:`FIELDS`**：它不该拦派活——硬拦的结果是人开始凑一句废话交差。
RATIONALE_LABEL = "为什么这么拆"

#: 缺这一段的代价（只讲代价，不讲规矩）。
_RATIONALE_COST = "没写为什么拆、替它定死了哪几条，它可能照着字面做偏，回来你得从成品里反推它当初怎么想的"

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


def compose_brief(
    goal: str,
    deliverable: str = "",
    tools: str = "",
    bounds: str = "",
    rationale: str = "",
) -> str:
    """把四要素（＋软项「为什么这么拆」）拼成一段交给客席的正文。

    只拼**给了的**那几样：没给的不留空标题，免得它照着空标题自己编一段边界出来。
    ``rationale`` 排在最后：它解释的是前面那四段，得让它先看到活长什么样。
    """
    text = str(goal or "").strip()
    parts = [f"【目标】{text}" if text else ""]
    for key, label in FIELDS[1:]:
        value = str({"deliverable": deliverable, "tools": tools, "bounds": bounds}[key] or "").strip()
        if value:
            parts.append(f"【{label}】{value}")
    why = str(rationale or "").strip()
    if why:
        parts.append(f"【{RATIONALE_LABEL}】{why}")
    return "\n".join(part for part in parts if part)


def rationale_hint(rationale: str = "") -> str:
    """没写「为什么这么拆」时的一句话代价；写了就空串。"""
    return "" if str(rationale or "").strip() else _RATIONALE_COST


# ---------------------------------------------------------------------------
# 事后判据（``WP-DISPATCH-VERDICT``，11-25）：这一轮是不是伸手干了本该别人干的活
# ---------------------------------------------------------------------------
#
# 事前猜「这句话该不该派给谁」这件事，业界最好的模型也只有六成多
# （ProAgentBench 2026-02：64.4%，且低精确率会把人烦到关掉提醒）。
# 所以这里不猜意图，只结算事实：这一轮**调了哪些工具**、**派没派**。
# 按工具名判，不按用户话术判——裁定21.1 禁的是给话语建 keyword 表，不是给工具归面。

#: 伸手能力面：标签 → (工具名, 人话)。工具名稳定，加新工具往这里补一行。
OTHER_HANDS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("write", ("write_file", "patch", "apply_patch"), "往盘上写东西"),
    ("terminal", ("shell_executor", "run_terminal", "terminal_exec"), "跑命令"),
    ("browser", ("browser_navigate", "browser_click", "web_form"), "开浏览器办事"),
    ("computer", ("computer_use", "computer_click"), "操作别的软件"),
)

#: 这一轮里出现它，就算派过了（工具名，不是话术）。
DISPATCH_TOOL_NAMES: frozenset[str] = frozenset({
    "external_offload_run",
    "external_session_send",
    "delegate_task",
})


def hand_for(tool_name: str) -> Optional[str]:
    """这条工具是在替谁伸手（返回能力面标签）；只读工具返回 ``None``。"""
    name = str(tool_name or "").strip()
    for label, names, _ in OTHER_HANDS:
        if name in names:
            return label
    return None


def hands_touched(tool_names: Iterable[str]) -> list[str]:
    """这一轮碰到哪几个能力面（去重，按 :data:`OTHER_HANDS` 顺序）。"""
    seen = {hand_for(name) for name in tool_names}
    return [label for label, _, _ in OTHER_HANDS if label in seen]


def missed_dispatch(tool_names: Iterable[str]) -> list[str]:
    """该派却没派：这一轮伸手干了、但没有一枚派活工具。返回能力面标签，空列表即没问题。

    自己动手不是错——派不出去的时候本来就该自己干。这里的产出只是一句话，
    目的是让「这活名册上有主」这件事在**干完之后**被看见，而不是事前拦一次。
    """
    names = {str(name or "").strip() for name in tool_names}
    if names & DISPATCH_TOOL_NAMES:
        return []
    return hands_touched(names)


def missed_hint(labels: Iterable[str], *, owners: int = 0) -> str:
    """把「该派没派」读成一句人话；没问题则空串。

    ``owners`` 只影响语气：名册那一摊有主就该派，没主自己干本来就对。
    """
    human = {label: text for label, _, text in OTHER_HANDS}
    doing = "、".join(text for text in (human.get(label) for label in dict.fromkeys(labels)) if text)
    if not doing:
        return ""
    if owners > 0:
        return f"这轮自己动了（{doing}），名册上那一摊有主（{owners} 位）：派过去比自己动手省事，也免得两处说法。"
    return f"这轮自己动了（{doing}），名册里没有接这一摊的人，自己干是对的。"
