"""把一段活交给**临时专家**（WP-L3-EXPERTS 第二拍）：四类角色，不进名册。

与 `delegate_task` 的区别只有两点：任务书前面多一段**职责**（它是谁），以及只给它
**那一层工具面**。专家仍然只是 `delegate_task` 的子会话——没有名册行、没有长期家。

用户 10-28 的口径：「不算名册里的 Agent。但是 L2 调用的时候对应的专家会出现在左侧栏。
点开可以看详细工作日志。」左栏那一格读的是 `$PLOBI_HOME/experts/<子会话>.json`
（这一刀写下的角色侧记）。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict

EXPERTS_DIR_ENV = "PLOBI_EXPERTS_DIR"


def experts_dir() -> Path:
    import os

    override = os.environ.get(EXPERTS_DIR_ENV, "").strip()
    if override:
        return Path(os.path.expanduser(override))
    from plobi_constants import get_plobi_home

    return get_plobi_home() / "experts"


def _record_role(child_session_id: str, role: str, goal: str) -> None:
    sid = str(child_session_id or "").strip()
    if not sid:
        return
    path = experts_dir() / f"{sid}.json"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"role": role, "goal": goal, "at": time.strftime("%Y-%m-%dT%H:%M:%S")},
                       ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError:
        pass


SCHEMA = {
    "name": "expert_dispatch",
    "description": (
        "把一段活交给一个**临时专家**（文件 / 浏览器 / 软件 / 电脑四类之一）。"
        "专家用完即丢、不进团队名单；它只拿到那一类的工具面，任务书前面会带上它的职责。"
        "整块、可独立验收、不需要本会话上下文的活才交给它；一句话能答的自己做。"
        "交完把它的结论与产出路径回报给用户。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "role": {
                "type": "string",
                "enum": ["file", "browser", "software", "computer"],
                "description": "文件 / 浏览器 / 软件 / 电脑。按这段活需要的手来选。",
            },
            "goal": {"type": "string", "description": "这段活要做到什么程度；说清交付物与验收方式。"},
            "context": {"type": "string", "description": "它需要的路径、约束、已知事实。"},
        },
        "required": ["role", "goal"],
    },
}


def handle(args: Dict[str, Any], **kw: Any) -> str:
    from plobi.delegation.expert_roles import role_spec, stamp_goal

    parent = kw.get("parent_agent")
    if parent is None:
        return json.dumps({"ok": False, "error": "expert_dispatch 需要父 Agent 上下文"}, ensure_ascii=False)

    role = str(args.get("role") or "").strip().lower()
    spec = role_spec(role)
    if spec is None:
        return json.dumps(
            {"ok": False, "error": "角色只有 file / browser / software / computer 四种"},
            ensure_ascii=False,
        )
    goal = str(args.get("goal") or "").strip()
    if not goal:
        return json.dumps({"ok": False, "error": "goal 不能为空"}, ensure_ascii=False)

    from tools.delegate_tool import delegate_task

    raw = delegate_task(
        goal=stamp_goal(role, goal),
        context=str(args.get("context") or "").strip() or None,
        role="leaf",
        parent_agent=parent,
        toolsets=list(spec["toolsets"]),
    )
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return raw

    results = payload.get("results") if isinstance(payload, dict) else None
    if isinstance(results, list):
        for entry in results:
            if isinstance(entry, dict):
                _record_role(entry.get("child_session_id", ""), role, goal)
        payload["expert"] = {"role": role, "label": spec["label"]}

    return json.dumps(payload, ensure_ascii=False)


from tools.registry import registry


def _check_parent() -> bool:
    # 派工永远可用；没有父 Agent 上下文时 handler 自己会拒（不动工具面）。
    return True


registry.register(
    name="expert_dispatch",
    toolset="delegation",
    schema=SCHEMA,
    handler=handle,
    check_fn=_check_parent,
    emoji="🧑‍🔬",
)
