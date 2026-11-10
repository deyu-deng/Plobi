"""临时专家的四类角色（WP-L3-EXPERTS 第二拍）：文件 / 浏览器 / 软件 / 电脑。

用户 10-28 的口径：专家**不进名册**，但 L2 派出去时要能在左栏看见、点开有工作日志。
角色只干两件事：给这个子智能体**一段职责**（它自己知道自己是谁）和**一层工具面**
（只看该看的工具，别把整个工具箱背过去）。图标与名字是给人看的，不上模型。

11-04 用户补的三条（这一版落在这里）：
- **专家是 L3，不是普通子代理**：它们能自己再派普通子代理，所以角色一律 `orchestrator`；
- **每类角色可以有自己的模型**，跟 L2 不一样也行（存在 `experts/roles.json`，
  没配就继承父 Agent——与普通子代理同一条默认）；
- 普通子代理（不管谁派的）进底栏 overlay，L3 自己的工作日志进主区卡片。
  区分靠侧记：带角色侧记的是 L3，不带的才是普通子代理。

新增一类角色的判据：有独立的工具面 + 一句说得清的职责；否则别开新角色。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Optional

ROLES: dict[str, dict[str, Any]] = {
    "file": {
        "label": "文件",
        "mark": "文",
        "toolsets": ["file"],
        "duty": (
            "你是文件与资料专家：只负责读、整理、写文件（资料汇总、格式转换、按目录归档）。"
            "不跑命令、不联网。做完把产出路径与一句结论交回去。"
        ),
    },
    "browser": {
        "label": "浏览器",
        "mark": "浏",
        "toolsets": ["browser", "web"],
        "duty": (
            "你是浏览器专家：只负责打开网页、点按、抄下页面上的事实（含来源 URL 与抓取时间）。"
            "不改本地文件、不跑命令；抄不到就说抄不到。"
        ),
    },
    "software": {
        "label": "软件",
        "mark": "软",
        "toolsets": ["terminal", "file"],
        "duty": (
            "你是软件与脚本专家：只负责装/跑/修本机软件与脚本，交付时给命令、退出码与原始输出。"
            "不联网找资料、不改网页。"
        ),
    },
    "computer": {
        "label": "电脑",
        "mark": "电",
        "toolsets": ["terminal", "computer_use"],
        "duty": (
            "你是电脑运维专家：只负责本机的系统设置、进程与窗口操作；动作前先说你要动什么，"
            "破坏性操作一律先问。"
        ),
    },
}

ORDER = ("file", "browser", "software", "computer")


def role_spec(key: str) -> Optional[dict[str, Any]]:
    spec = ROLES.get(str(key or "").strip().lower())
    return dict(spec) if spec else None


def role_rows() -> list[dict[str, Any]]:
    """给人看的名册（设置/说明用）：键、名字、职责、工具面。"""
    return [
        {
            "key": key,
            "label": ROLES[key]["label"],
            "mark": ROLES[key]["mark"],
            "duty": ROLES[key]["duty"],
            "toolsets": list(ROLES[key]["toolsets"]),
        }
        for key in ORDER
    ]


def stamp_goal(key: str, goal: str) -> str:
    """把职责放在任务书最前面——子智能体一睁眼就知道自己是谁。"""
    spec = role_spec(key)
    body = (goal or "").strip()
    if not spec:
        return body

    return f"{spec['duty']}\n\n——\n{body}"


def body_of(key: str, text: str) -> str:
    """把盖过章的任务书取回人写的那一段（左栏标题、进度标签都读它）。"""
    spec = role_spec(key)
    body = str(text or "")
    if not spec:
        return body.strip()
    prefix = f"{spec['duty']}\n\n——\n"
    return body[len(prefix):].strip() if body.startswith(prefix) else body.strip()


def experts_dir() -> Path:
    """角色侧记的落点：`$PLOBI_HOME/experts/<子会话>.json`（左栏那一格读它）。"""
    override = os.environ.get("PLOBI_EXPERTS_DIR", "").strip()
    if override:
        return Path(os.path.expanduser(override))
    from plobi_constants import get_plobi_home

    return get_plobi_home() / "experts"


def roles_config_path() -> Path:
    """四类角色各自的模型：`$PLOBI_HOME/experts/roles.json`。

    形状：`{"software": {"model": "provider/model"}, ...}`。**没有这一格＝继承父 Agent**，
    与普通子代理同一条默认——不给专家配模型时，它就该跟 L2 用同一个。
    """
    return experts_dir() / "roles.json"


def load_role_models() -> dict[str, str]:
    """角色 → `provider/model`。读不到/坏档一律当「没配」，不拖垮派工。"""
    try:
        payload = json.loads(roles_config_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}

    out: dict[str, str] = {}
    for key, value in payload.items():
        role = str(key or "").strip().lower()
        if role not in ROLES:
            continue
        model = ""
        if isinstance(value, dict):
            model = str(value.get("model") or "").strip()
        elif isinstance(value, str):
            model = value.strip()
        if model:
            out[role] = model
    return out


def model_for_role(key: str) -> Optional[str]:
    """这一类角色的模型；没配就是 None（＝继承父 Agent）。"""
    role = str(key or "").strip().lower()
    return load_role_models().get(role) if role in ROLES else None


def save_role_model(key: str, model: Optional[str]) -> dict[str, str]:
    """写一类角色的模型。`model` 传空＝清掉覆盖，回到继承父 Agent。

    整档重写是有意的：这份文件只归本模块管，没有别人往里写行。
    """
    role = str(key or "").strip().lower()
    if role not in ROLES:
        raise ValueError(f"未知专家角色 {key!r}：只有 file / browser / software / computer 四种")

    current = load_role_models()
    value = str(model or "").strip()
    if value:
        current[role] = value
    else:
        current.pop(role, None)

    path = roles_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {name: {"model": value} for name, value in current.items()}
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return current


def record_role(child_session_id: str, role: str, goal: str, model: Optional[str] = None,
               ticket_id: str = "") -> None:
    sid = str(child_session_id or "").strip()
    if not sid or role not in ROLES:
        return
    path = experts_dir() / f"{sid}.json"
    payload: dict[str, Any] = {
        "role": role,
        "goal": goal,
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    # 这一趟实际用的模型：左栏要能看出「这个专家跟 L2 不是同一个脑子」。
    if model:
        payload["model"] = str(model)
    # 派发前那张预授权票的 id：左栏徽标与详情按它回查授权范围。
    if ticket_id:
        payload["ticket_id"] = str(ticket_id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
