"""临时专家的四类角色（WP-L3-EXPERTS 第二拍）：文件 / 浏览器 / 软件 / 电脑。

用户 10-28 的口径：专家**不进名册**，但 L2 派出去时要能在左栏看见、点开有工作日志。
角色只干两件事：给这个子智能体**一段职责**（它自己知道自己是谁）和**一层工具面**
（只看该看的工具，别把整个工具箱背过去）。图标与名字是给人看的，不上模型。

11-04 用户补的三条（这一版落在这里）：
- **专家是 L3，不是普通子代理**：它们能自己再派普通子代理，所以角色一律 `orchestrator`；
- **每类角色可以有自己的模型**，跟 L2 不一样也行（存在 `virtuosos/roles.json`，
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

#: 名手的分类只有这一处真源：**键与顺序**（2026-11-15 收口）。
#:
#: 以前「键、中文名、职责、工具面」四件事挤在一个 ``ROLES`` 字典里，前端另抄一份
#: ``VIRTUOSO_ROLES`` 和一份 ``VirtuosoRoleKey`` 联合类型：加一类要改三处，漏一处就得到
#: 一行永远点不开的空行。现在拆成四张同键的平表——``TRADES`` 定键与序、``LABELS`` 定
#: 中文名、``DUTIES`` 定职责、``TOOLSETS`` 定工具面；``ROLES`` 只是它们的派生视图，
#: **别在 ``ROLES`` 里写内容**。前端那份镜像在 ``apps/desktop/src/shared/virtuoso-trades.ts``，
#: 键集合与顺序由 ``virtuoso-trades.test.ts`` 与这张表锁死。
TRADES: tuple[str, ...] = ("file", "browser", "software", "computer")

#: 中文名（左栏那一格的字在 ``MARKS``）。只给人看，不上模型。
LABELS: dict[str, str] = {
    "file": "文件",
    "browser": "浏览器",
    "software": "软件",
    "computer": "电脑",
}

MARKS: dict[str, str] = {
    "file": "文",
    "browser": "浏",
    "software": "软",
    "computer": "电",
}

#: 一句话职责：盖在任务书最前面，子智能体一睁眼就知道自己是谁。
#: 裁定 223：开头用**名手**这个角色名（它是这一跳的身份），「专家」留在对象名里。
DUTIES: dict[str, str] = {
    "file": (
        "你是名手（文件与资料）：只负责读、整理、写文件（资料汇总、格式转换、按目录归档）。"
        "不跑命令、不联网。做完把产出路径与一句结论交回去。"
    ),
    "browser": (
        "你是名手（浏览器）：只负责打开网页、点按、抄下页面上的事实（含来源 URL 与抓取时间）。"
        "不改本地文件、不跑命令；抄不到就说抄不到。"
    ),
    "software": (
        "你是名手（软件与脚本）：只负责装/跑/修本机软件与脚本，交付时给命令、退出码与原始输出。"
        "不联网找资料、不改网页。"
    ),
    "computer": (
        "你是名手（电脑运维）：只负责本机的系统设置、进程与窗口操作；动作前先说你要动什么，"
        "破坏性操作一律先问。"
    ),
}

#: **能力项**（人话）——设置面勾的就是这些；一个能力项＝一组工具门类。
#:
#: 粒度说明：能力项落在**门类**这一层（`file` 那一门类含读写搜索几把）⇒
#: 「读文件」与「写文件」都映射到 `file` 门类，勾掉其中一个会连带另一把。
#: 想要更细的粒度得把门类拆开（那是另一次改法），本轮先把**能勾、能看、能说人话**做出来。
CAPABILITIES: dict[str, str] = {
    "read_files": "读文件",
    "write_files": "写文件",
    "run_commands": "跑命令",
    "browse": "上网",
    "read_pages": "抄网页",
    "see_images": "看图",
    "operate_desktop": "操作窗口",
}

#: 能力项 → 工具门类。**工具面由它派生**（别在角色表里再写一遍门类）⇒ 一处维护。
CAPABILITY_TOOLSETS: dict[str, list[str]] = {
    "read_files": ["file"],
    "write_files": ["file"],
    "run_commands": ["terminal"],
    "browse": ["browser"],
    "read_pages": ["web"],
    "see_images": ["vision"],
    "operate_desktop": ["computer_use"],
}

#: 每个角色**默认**开哪些能力。**与 11-15 之前的工具面逐字一致**（一个字节没变）——
#: 「看图」进得了池子，但**默认不开**（开不开由设置面那一格勾）。
ROLE_CAPABILITIES: dict[str, tuple[str, ...]] = {
    "file": ("read_files", "write_files"),
    "browser": ("browse", "read_pages"),
    "software": ("run_commands", "read_files", "write_files"),
    "computer": ("run_commands", "operate_desktop"),
}


def _derive_toolsets(capabilities) -> list[str]:
    """能力清单 → 工具门类（按能力顺序去重 ⇒ 与旧表逐字同序）。"""
    out: list[str] = []
    for capability in capabilities:
        for toolset in CAPABILITY_TOOLSETS.get(capability, ()):
            if toolset not in out:
                out.append(toolset)
    return out


#: 工具面：只看该看的工具，别把整个工具箱背过去。**由能力派生**（11-15 那次收口把
#: 「键/名/职责/工具面」拆成平表；这一刀再拆出一层「能力」⇒ 工具面不再手写）。
TOOLSETS: dict[str, list[str]] = {
    key: _derive_toolsets(capabilities) for key, capabilities in ROLE_CAPABILITIES.items()
}

#: 派生视图（键 → 上面几表的一项）：runner 与 router 读它，内容不在这里写。
ROLES: dict[str, dict[str, Any]] = {
    key: {
        "label": LABELS[key],
        "mark": MARKS[key],
        "toolsets": list(TOOLSETS[key]),
        "duty": DUTIES[key],
    }
    for key in TRADES
}

#: 旧名，等价于 ``TRADES``；新代码直接读 ``TRADES``。
ORDER = TRADES


def role_spec(key: str) -> Optional[dict[str, Any]]:
    spec = ROLES.get(str(key or "").strip().lower())
    return dict(spec) if spec else None


def role_rows() -> list[dict[str, Any]]:
    """给人看的名册（设置/说明用）：键、名字、职责、工具面、能力。

    顺序只读 ``TRADES``——前端把它当唯一真相（``virtuoso-trades.test.ts`` 对表）。
    """
    return [
        {
            "key": key,
            "label": ROLES[key]["label"],
            "mark": ROLES[key]["mark"],
            "duty": ROLES[key]["duty"],
            # ``toolsets``＝**默认**那一层（既有消费方指着它，别改）；
            # ``effective_toolsets``＝它**现在**真拿到的（减掉自己的黑名单与全局硬压制）。
            "toolsets": list(ROLES[key]["toolsets"]),
            "effective_toolsets": effective_toolsets(key),
            "capabilities": role_capabilities(key),
            "effective_capabilities": effective_capabilities(key),
            "missing_capabilities": missing_capabilities(key),
            "disabled_capabilities": sorted(role_disabled_capabilities(key)),
        }
        for key in TRADES
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


def _migrate_legacy_dir(home: Path) -> Path:
    """旧名目录 ``experts/`` 就地改名成 ``virtuosos/``（11-25 改名那一刀）。

    只做到这一件事：**老目录里有 ``*.json`` 就整体搬过去**，搬不动（占用/权限）就让
    读侧自己落到空目录，不抛错——侧记丢了最多是那一格显示「没配」。
    """
    target = home / "virtuosos"
    legacy = home / "experts"
    try:
        if legacy.is_dir() and not target.exists():
            legacy.rename(target)
    except OSError:
        pass
    return target


def virtuosos_dir() -> Path:
    """角色侧记的落点：`$PLOBI_HOME/virtuosos/<子会话>.json`（左栏那一格读它）。"""
    override = os.environ.get("PLOBI_VIRTUOSOS_DIR", "").strip()
    if override:
        return Path(os.path.expanduser(override))
    from plobi_constants import get_plobi_home

    return _migrate_legacy_dir(get_plobi_home())


def roles_config_path() -> Path:
    """四类角色各自的设置：`$PLOBI_HOME/virtuosos/roles.json`。

    形状：`{"software": {"model": "provider/model", "disabled_capabilities": [...]}}`。

    - ``model`` 没有这一格＝**继承父 Agent**（与普通子代理同一条默认）；
    - ``disabled_capabilities`` 没有这一格＝**默认全开**（角色表里那份默认能力）。

    ⚠ **能力的底永远是设置面那一份全局真源**（技能目录／工具门类／MCP 全局表）；
    这一份只是**角色级的减项**（名手自己的黑名单）——它**不继承 principal 的工具设置**。
    """
    return virtuosos_dir() / "roles.json"


def load_role_settings() -> dict[str, dict]:
    """角色 → 它的设置（``model`` ＋ ``disabled_capabilities``）。读不到/坏档一律当「没配」。"""
    try:
        payload = json.loads(roles_config_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}

    out: dict[str, dict] = {}
    for key, value in payload.items():
        role = str(key or "").strip().lower()
        if role not in ROLES:
            continue
        if isinstance(value, str):
            value = {"model": value}
        if not isinstance(value, dict):
            continue
        settings: dict[str, Any] = {}
        model = str(value.get("model") or "").strip()
        if model:
            settings["model"] = model
        disabled = value.get("disabled_capabilities")
        if isinstance(disabled, (list, tuple)):
            settings["disabled_capabilities"] = [
                str(c).strip() for c in disabled if str(c).strip()
            ]
        out[role] = settings
    return out


def load_role_models() -> dict[str, str]:
    """角色 → `provider/model`。读不到/坏档一律当「没配」，不拖垮派工。"""
    return {
        role: str(settings.get("model") or "")
        for role, settings in load_role_settings().items()
        if settings.get("model")
    }


def role_disabled_capabilities(key: str) -> set[str]:
    """这个名手**自己**关掉的能力（角色级黑名单；**不是** principal 那份）。"""
    role = str(key or "").strip().lower()
    settings = load_role_settings().get(role) or {}
    return {str(c).strip().casefold() for c in settings.get("disabled_capabilities") or []}


def role_capabilities(key: str) -> list[str]:
    """这个角色**默认**的能力（人话标签）。"""
    role = str(key or "").strip().lower()
    return [
        CAPABILITIES.get(capability, capability)
        for capability in ROLE_CAPABILITIES.get(role, ())
    ]


def _capability_alive(capability: str, disabled: set, blocked: set) -> bool:
    """这一项能力现在还在吗？两个减项：它自己的黑名单、全局硬压制（真源的一部分）。"""
    if capability.casefold() in disabled:
        return False
    toolsets = CAPABILITY_TOOLSETS.get(capability, ())
    if toolsets and all(name.casefold() in blocked for name in toolsets):
        return False
    return True


def effective_capabilities(key: str) -> list[str]:
    """这个名手**现在**有的能力（默认 − 它自己的黑名单 − 全局硬压制），人话标签。"""
    role = str(key or "").strip().lower()
    disabled = role_disabled_capabilities(role)
    blocked = _global_disabled_toolsets()
    return [
        CAPABILITIES.get(capability, capability)
        for capability in ROLE_CAPABILITIES.get(role, ())
        if _capability_alive(capability, disabled, blocked)
    ]


def _global_disabled_toolsets() -> set[str]:
    """全局硬压制（设置面那份 ``agent.disabled_toolsets``）——与 principal 无关。"""
    try:
        from plobi_cli.config import load_config

        config = load_config() or {}
    except Exception:  # noqa: BLE001 - 配置读不动就当没有硬压制
        return set()
    agent_cfg = config.get("agent") or {}
    return {
        str(name).strip().casefold()
        for name in (agent_cfg.get("disabled_toolsets") or [])
        if str(name).strip()
    }


def effective_toolsets(key: str) -> list[str]:
    """这个名手现在拿到的**工具门类**（能力派生 − 全局硬压制）。

    ⚠ **不与 principal 的工具集求交集**——名手自带工具箱（用户 11-15 12:5x 定调：
    能力的底永远是设置面那一份全局真源，名手只减**自己**的黑名单，不继承 principal
    的工具设置）。子代理那一支仍旧继承父的（那是另一族）。
    """
    role = str(key or "").strip().lower()
    disabled = role_disabled_capabilities(role)
    blocked = _global_disabled_toolsets()
    capabilities = [
        capability
        for capability in ROLE_CAPABILITIES.get(role, ())
        if _capability_alive(capability, disabled, blocked)
    ]
    return [name for name in _derive_toolsets(capabilities) if name.casefold() not in blocked]


def missing_capabilities(key: str) -> list[str]:
    """这个名手**现在拿不到**的能力（默认有、现在没有），人话标签。

    少了哪样要写出来——「派出去的与承诺的不符」是最容易被忽略的一种坑
    （角色自己的黑名单关掉的、全局硬压制砍掉的，都算少）。
    """
    role = str(key or "").strip().lower()
    disabled = role_disabled_capabilities(role)
    blocked = _global_disabled_toolsets()
    return [
        CAPABILITIES.get(capability, capability)
        for capability in ROLE_CAPABILITIES.get(role, ())
        if not _capability_alive(capability, disabled, blocked)
    ]


def model_for_role(key: str) -> Optional[str]:
    """这一类角色的模型；没配就是 None（＝继承父 Agent）。"""
    role = str(key or "").strip().lower()
    return load_role_models().get(role) if role in ROLES else None


def _write_role_settings(settings: dict[str, dict]) -> dict[str, dict]:
    """整档写回。**这一份只归本模块管**，没有别人往里写行（与旧的模型表同一条口径）。"""
    path = roles_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {}
    for role, values in settings.items():
        entry: dict[str, Any] = {}
        model = str(values.get("model") or "").strip()
        if model:
            entry["model"] = model
        disabled = [str(c).strip() for c in values.get("disabled_capabilities") or [] if str(c).strip()]
        if disabled:
            entry["disabled_capabilities"] = disabled
        payload[role] = entry
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return settings


def save_role_model(key: str, model: Optional[str]) -> dict[str, str]:
    """写一类角色的模型。`model` 传空＝清掉覆盖，回到继承父 Agent。

    **只动这一格的 ``model``**，同一格里的 ``disabled_capabilities`` 原样留着。
    """
    role = str(key or "").strip().lower()
    if role not in ROLES:
        raise ValueError(f"未知专家角色 {key!r}：只有 file / browser / software / computer 四种")

    settings = load_role_settings()
    current = dict(settings.get(role) or {})
    value = str(model or "").strip()
    if value:
        current["model"] = value
    else:
        current.pop("model", None)
    settings[role] = current
    _write_role_settings(settings)
    return load_role_models()


def save_role_disabled_capabilities(key: str, disabled: list[str]) -> dict[str, list[str]]:
    """写这个名手**自己**的黑名单（能力项名）。传空＝回到默认全开。

    只动这一格的 ``disabled_capabilities``，同一格里的 ``model`` 原样留着。
    """
    role = str(key or "").strip().lower()
    if role not in ROLES:
        raise ValueError(f"未知专家角色 {key!r}：只有 file / browser / software / computer 四种")

    settings = load_role_settings()
    current = dict(settings.get(role) or {})
    names = sorted({str(c).strip().casefold() for c in (disabled or []) if str(c).strip()})
    unknown = [c for c in names if c not in CAPABILITIES]
    if unknown:
        raise ValueError(f"不认得的能力项：{'、'.join(unknown)}（认得：{'、'.join(sorted(CAPABILITIES))}）")
    if names:
        current["disabled_capabilities"] = names
    else:
        current.pop("disabled_capabilities", None)
    settings[role] = current
    _write_role_settings(settings)
    return {r: list(v.get("disabled_capabilities") or []) for r, v in load_role_settings().items()}


def record_role(child_session_id: str, role: str, goal: str, model: Optional[str] = None,
               ticket_id: str = "") -> None:
    sid = str(child_session_id or "").strip()
    if not sid or role not in ROLES:
        return
    path = virtuosos_dir() / f"{sid}.json"
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
