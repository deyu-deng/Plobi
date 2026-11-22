"""外部 AI 软件会话的**能力真源**：谁在名册里、每一家能做什么。

之前四张 schema 各写了一份 vendor 名单（`mark` 三家、`rename` 五家、
`scan` 三家、`run` 三家），同一件事四种说法。这里收成一张表：

* 工具描述里的「哪几家」由 `vendor_list_text()` 生成，不再手抄；
* `scan` 每一行、每个 source 都带 `capabilities`，模型一次扫描就知道哪条能说话、
  能改什么，不用先试一次再吃报错；
* 归一化权限档（`read_only` / `edit` / `full_auto`）在 `permission_alias()` 里
  映射到各家的真名——**档名各家不通用**（CodeBuddy 是 `bypassPermissions`，
  Qoder 是 `bypass_permissions`），这层映射不该甩给模型去猜。

新增一家软件：在这里加一行 + 在 `external_sessions.py` 加一个读盘适配器，
**不需要新工具**。这是「通用原子工具」形状真正的收益。

边界说清楚：`model` / `permission` 这两格是 **Plobi 侧的会话预设**，
不是去改对方软件里那条会话——对方多数根本不允许改已创建会话的模型与权限。
`apply_on_send` 标出哪家续接时真能把这些预设带上去；今天一家都没有，
所以工具会如实说「已记下，但续接时暂不能自动应用」，绝不静默成功。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# 归一化权限档：模型只说这三档，各家真名由 permission_alias 翻译。
PERMISSION_LEVELS = ("read_only", "edit", "full_auto")

# 动作名 → 能力位。工具的闸门与 scan 的能力位都用这一份，别处不许再列一份。
ACTIONS = ("read", "rename", "edit", "model", "permission", "send", "attachments", "archive", "delete", "mark",
            # 「ACP 腿派出去的活能不能接着聊」（11-17）：ACP 的 ``session/load`` 是协议级的，
            # 这一格只登记**实测过**的那几家 ⇒ 读数真源在 ``agent/vendor_registry.py``
            # 那一行的 ``acp_note``。
            "acp_resume")


@dataclass(frozen=True)
class VendorCapabilities:
    vendor: str
    label: str
    #: 一句人话：**这家的会话在我们这边是什么形状、能拿它干什么**。
    #: 只写已核实的事实（读法形状 + 能不能接着说话），不抄厂商宣传语——
    #: 名册是交给 Principal 自己挑的，它要的是「什么时候该找这一家」，不是广告。
    blurb: str = ""
    # 各家共有的账本操作（改名/归档/删除/标记都在 Plobi 这一侧，与对方软件无关）
    ledger_actions: tuple[str, ...] = ("read", "rename", "archive", "delete", "mark")
    # 能不能往那条会话里接着说话
    send: bool = False
    # 说话时能不能带附件
    attachments: bool = False
    # 能不能编辑会话里的消息内容（不是改标题，标题是 rename）
    edit: bool = False
    # 能不能存「下次续接用什么模型/权限档」的会话预设
    model: bool = True
    permission: bool = True
    #: **ACP 腿派出去的活能不能接着聊**（11-17 实测：ACP 的 ``session/load`` 真能续）。
    #: 今天只有 opencode 一家验通了「派完 → 落进它自己的会话库 → 会话面看得见 → 能接着说」。
    acp_resume: bool = False
    # 续接时能不能真把预设带上去。今天没有一家验证过，一律 False。
    apply_on_send: bool = False
    # 归一化档 → 这家 CLI 的真名（只用于派新活，见 external_offload.run_task）
    permission_aliases: dict[str, str] = field(default_factory=dict)

    def as_list(self) -> list[str]:
        """给 scan 用的紧凑能力位数组。"""
        out = list(self.ledger_actions)
        if self.send:
            out.append("send")
        if self.attachments:
            out.append("attachments")
        if self.edit:
            out.append("edit")
        if self.model:
            out.append("model")
        if self.permission:
            out.append("permission")
        if self.apply_on_send:
            out.append("apply_on_send")
        if self.acp_resume:
            out.append("acp_resume")

        return out

    def as_dict(self) -> dict:
        return {
            "acp_resume": self.acp_resume,
            "apply_on_send": self.apply_on_send,
            "attachments": self.attachments,
            "capabilities": self.as_list(),
            "edit": self.edit,
            "label": self.label,
            "model": self.model,
            "permission": self.permission,
            "send": self.send,
            "vendor": self.vendor,
        }


#: 顺序与 `plobi/delegation/external_sessions.py::session_sources()` 一致。
VENDORS: dict[str, VendorCapabilities] = {
    "qoder": VendorCapabilities(
        vendor="qoder",
        label="Qoder",
        blurb="会话按项目目录落成 jsonl；项目目录在，就能接着说话",
        send=True,
        permission_aliases={
            "read_only": "default",
            "edit": "accept_edits",
            "full_auto": "bypass_permissions",
        },
    ),
    "codebuddy": VendorCapabilities(
        vendor="codebuddy",
        label="CodeBuddy",
        blurb="会话按项目目录落成 jsonl，它自带 --resume 回跳；从 Plobi 里还不能往里说话",
        permission_aliases={
            "read_only": "plan",
            "edit": "acceptEdits",
            "full_auto": "bypassPermissions",
        },
    ),
    "marvis": VendorCapabilities(vendor="marvis", label="Marvis", blurb="本机 Marvis 桌面端，会话与正文都在它的 sqlite 库里；只能读，不接派活"),
    "cursor": VendorCapabilities(vendor="cursor", label="Cursor", blurb="Cursor 编辑器，会话与正文压在它自己的 KV 大块里；只能读"),
    "agy": VendorCapabilities(vendor="agy", label="agy", blurb="会话落 sqlite，正文按 step 一条条记着；只能读"),
    # OpenCode：**能续接说话**（现读 `opencode run --help`：`--session <id>` 续接那条会话）。
    # 它的回包是**逐行 JSON 事件**（不是 Qoder 那种一个对象）⇒ 解析在
    # `external_sessions.py::_parse_opencode_stream` 里，不在能力表这一层。
    # 11-17 实测：它的 ACP server 宣告 ``loadSession`` ⇒ ``session/load`` 真能续；
    # 它开出来的会话**落进它自己的会话库** ⇒ 会话面看得见、能接着说。
    "opencode": VendorCapabilities(vendor="opencode", label="OpenCode", blurb="会话落 sqlite；ACP 那条腿实测能续接 ⇒ 派完活还能接着说", send=True, acp_resume=True),
    # ── 11-15 新接会话腿的那三家（都**只读**：回跳旗标没实测过）────────────
    "kiro": VendorCapabilities(vendor="kiro", label="Kiro", blurb="会话直接躺在根目录的 jsonl 里，没有项目那一层；只能读"),
    "copilot": VendorCapabilities(vendor="copilot", label="GitHub Copilot CLI", blurb="会话落 sqlite，正文按一问一答成对记着；只能读"),
    # Qoder 中国版：另一件安装（`product: qodercn`）⇒ 单列一家，不并进 qoder。
    "qoder-cn": VendorCapabilities(vendor="qoder-cn", label="Qoder 中国版", blurb="Qoder 中国版是另一件安装（账号、二进制、会话库都与 qoder 不重叠）；只能读"),
    # 11-15 再接会话腿的三家（都**只读**：回跳旗标没实测过）。
    "gemini": VendorCapabilities(vendor="gemini", label="Gemini CLI", blurb="会话在项目目录的 chats/ 下，一条一个 jsonl；只能读"),
    "kilo": VendorCapabilities(vendor="kilo", label="Kilo Code", blurb="会话落 sqlite，正文按部件（parts）记着；只能读"),
    "cline": VendorCapabilities(vendor="cline", label="Cline", blurb="一个会话一个目录，正文在 messages 里；只能读"),
}


def capabilities(vendor: str) -> Optional[VendorCapabilities]:
    key = str(vendor or "").strip().lower()
    return VENDORS.get(key)


def vendor_list_text() -> str:
    """给 schema 描述用的「哪几家」——唯一出处，杜绝四份名单漂移。"""
    return " / ".join(spec.vendor for spec in VENDORS.values())


def vendor_names() -> tuple[str, ...]:
    return tuple(VENDORS)


def supports(vendor: str, action: str) -> bool:
    """闸门：不支持就当场报错，不许静默成功。"""
    spec = capabilities(vendor)
    if spec is None or action not in ACTIONS:
        return False
    if action in spec.ledger_actions:
        return True
    return bool(getattr(spec, action, False))


def unsupported_note(vendor: str, action: str) -> str:
    """给模型看的一句人话：说清是「不认识这家」还是「这家不支持这个动作」。"""
    spec = capabilities(vendor)
    if spec is None:
        return f"不认识这家软件 {vendor!r}；可操作的：{vendor_list_text()}"
    readable = ", ".join(spec.as_list())
    return f"{spec.label} 不支持「{action}」；这条会话目前能做的：{readable}"


def permission_alias(vendor: str, level: str) -> Optional[str]:
    """归一化档 → 这家 CLI 的真名。没登记就返回 None（不许拿别家的档名凑）。"""
    spec = capabilities(vendor)
    if spec is None:
        return None
    return spec.permission_aliases.get(str(level or "").strip().lower())


def permission_levels_text() -> str:
    return " / ".join(PERMISSION_LEVELS)
