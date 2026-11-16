"""外包 provider 的**真源表**：加一家只改这一处，其余处处派生。

用户 11-15 10:4x 原话：「**所以你有没有做到一个统一的拓展性强入口。不要硬编码提供商。
然后以后在一个地方添加一个provider多处直接同步。你懂我意思吗？**」

这一刀把「加一家要动三张表 ＋ 六个函数」收成**一张表、一行一家**：

* :data:`VENDOR_ROWS` —— 一行一家，装下这一家**特有**的事实：
  怎么起它（命令、argv 模板、权限档）、ACP 怎么问它、能不能回跳、
  登录痕迹落在哪儿、它的会话落在哪儿与字段怎么映射；
* 四张出口表全部**由它派生**（公共形状一个字不改 ⇒ 调用方零改动）：

  ==============================  ==========================================
  出口表                          谁在用
  ==============================  ==========================================
  :data:`EXTERNAL_AGENTS`          通道表（也是屏上名册的源头）
  :data:`LOGIN_HINTS`              登录痕迹表（``tools/offload_tools.py``）
  :data:`RESUME_ARGV`              回跳旗标表（控制台 ``OFFLOAD_RESUME_ARGV``）
  :data:`OFFLOAD_CONTRACT`         契约表（``plobi/delegation/offload_contract.py``）
  ==============================  ==========================================

**加一家 ⇒ 只在 :data:`VENDOR_ROWS` 加一行**，其余处处自己长出来。
这条由 ``tests/plobi/test_vendor_registry.py`` 那条**性质测试**钉住
（「加一行 ⇒ 四张出口表全都长出这一家」）——不是嘴上说的，是可查的。

⚠️ 这一族的老规矩，一条都不许越：

* **没登记 ⇒ 探不到（``None``），不判「没登录」**（那是「我们没核到」，不是「它没登录」）；
* **有文件 ≠ 登上了**：本地痕迹那一列只当提示，不当结论；
* **没实测过的回跳旗标不许编** —— 表里写的就是 :data:`NO_RESUME_FLAG` 那一句。
"""

from __future__ import annotations

import dataclasses
import os
import sys
from pathlib import Path
from typing import Any, Optional

# ── 权限档（CodeBuddy 那一套是实测出来的，Qoder 那一套拼写不一样）──────────

#: CodeBuddy 自己 ``--help`` 列的档位。哪一档真能零提问地干活是**实测事实**，
#: 不是读说明书读出来的（见 :data:`DEFAULT_PERMISSION_MODE` 的注释）。
PERMISSION_MODES = ("acceptEdits", "bypassPermissions", "default", "plan", "dontAsk", "auto")

#: 默认给**实测过真写得出文件**的那一档。``dontAsk`` 是第一猜：两趟产品路径的跑
#: （22:2x 与 19:0x）都 exit 0、烧掉约 114,700 输入 token、零产出，它自己回的是
#: Write/Bash/PowerShell 都被拒 ⇒ ``bypassPermissions`` 才是真干活的那一档。
DEFAULT_PERMISSION_MODE = "bypassPermissions"

PERMISSION_MODE_ENV = "PLOBI_OFFLOAD_CODEBUDDY_PERMISSION_MODE"
COMMAND_ENV = "PLOBI_OFFLOAD_CODEBUDDY_COMMAND"
EXTRA_ARGS_ENV = "PLOBI_OFFLOAD_CODEBUDDY_EXTRA_ARGS"

#: Qoder CLI 那一家的结构性事实。命令名是 ``qodercli``（``qoder`` 那个垫片会先经过
#: ``~/.qoder/entry/qoder-dispatcher.ps1`` 再回到 ``qodercli``，两条都认，但表里只写一个真名）。
QODER_COMMAND_ENV = "PLOBI_OFFLOAD_QODER_COMMAND"
QODER_PERMISSION_MODE_ENV = "PLOBI_OFFLOAD_QODER_PERMISSION_MODE"
QODER_EXTRA_ARGS_ENV = "PLOBI_OFFLOAD_QODER_EXTRA_ARGS"
#: 它自己 ``--help`` 列的档位。**拼写与 CodeBuddy 不是一套**（``bypass_permissions`` vs
#: ``bypassPermissions``），所以每家各有一份名单，谁也不许拿另一家的名单去校验。
QODER_PERMISSION_MODES = ("default", "accept_edits", "bypass_permissions", "dont_ask", "auto")
#: 默认给**最保守那一档**，不是最开的那一档。CodeBuddy 那边默认放开是有实测依据的
#: （放开才真写文件）；Qoder 这边没测过，未测就选最开的档＝拿他的仓库当试验场。
DEFAULT_QODER_PERMISSION_MODE = "default"
#: Qoder Desktop 起子进程时带这三个变量。CLI 一看到就认定自己是被人从 Agent SDK 里起的，
#: 直接要求 ``--print --input-format stream-json --output-format stream-json`` 并拒绝普通调用
#: （``sdk_invalid_args``）。所以派给它的活必须在把这份环境交出去之前把它们洗掉。
QODER_SDK_ENV = ("QODER_AGENT_SDK_ENTRYPOINT", "QODER_AGENT_SDK_DAEMON", "QODER_AGENT_SDK_VERSION")

#: agy 没有权限档阶梯 —— 一个开关自动批准一切，没有它就提问。哪一路是单开关，
#: 仍然从配置来（通道可以被调回受限那一档）。
AGY_SKIP_ENV = "PLOBI_OFFLOAD_AGY_SKIP_PERMISSIONS"
AGY_COMMAND_ENV = "PLOBI_OFFLOAD_AGY_COMMAND"
AGY_EXTRA_ARGS_ENV = "PLOBI_OFFLOAD_AGY_EXTRA_ARGS"

KIRO_COMMAND_ENV = "PLOBI_OFFLOAD_KIRO_COMMAND"
KIRO_EXTRA_ARGS_ENV = "PLOBI_OFFLOAD_KIRO_EXTRA_ARGS"
KIRO_TRUST_ENV = "PLOBI_OFFLOAD_KIRO_TRUST_ALL_TOOLS"
QWEN_COMMAND_ENV = "PLOBI_OFFLOAD_QWEN_COMMAND"
QWEN_EXTRA_ARGS_ENV = "PLOBI_OFFLOAD_QWEN_EXTRA_ARGS"
QWEN_YOLO_ENV = "PLOBI_OFFLOAD_QWEN_YOLO"
COPILOT_COMMAND_ENV = "PLOBI_OFFLOAD_COPILOT_COMMAND"
COPILOT_EXTRA_ARGS_ENV = "PLOBI_OFFLOAD_COPILOT_EXTRA_ARGS"
COPILOT_ALLOW_ALL_ENV = "PLOBI_OFFLOAD_COPILOT_ALLOW_ALL"
KILO_COMMAND_ENV = "PLOBI_OFFLOAD_KILO_COMMAND"
KILO_EXTRA_ARGS_ENV = "PLOBI_OFFLOAD_KILO_EXTRA_ARGS"
KILO_AUTO_ENV = "PLOBI_OFFLOAD_KILO_AUTO"
#: ``codex exec --help``：``--full-auto`` = 自动批准所有权限请求（沙箱内写文件 OK）。
CODEX_AUTO_ENV = "PLOBI_OFFLOAD_CODEX_FULL_AUTO"
CODEX_COMMAND_ENV = "PLOBI_OFFLOAD_CODEX_COMMAND"
CODEX_EXTRA_ARGS_ENV = "PLOBI_OFFLOAD_CODEX_EXTRA_ARGS"
#: ``claude --help``：``--dangerously-skip-permissions`` = 跳过全部权限提示（官方措辞）。
CLAUDE_SKIP_ENV = "PLOBI_OFFLOAD_CLAUDE_SKIP_PERMISSIONS"
CLAUDE_COMMAND_ENV = "PLOBI_OFFLOAD_CLAUDE_COMMAND"
CLAUDE_EXTRA_ARGS_ENV = "PLOBI_OFFLOAD_CLAUDE_EXTRA_ARGS"
CLINE_COMMAND_ENV = "PLOBI_OFFLOAD_CLINE_COMMAND"
CLINE_EXTRA_ARGS_ENV = "PLOBI_OFFLOAD_CLINE_EXTRA_ARGS"
CLINE_YOLO_ENV = "PLOBI_OFFLOAD_CLINE_YOLO"
GEMINI_COMMAND_ENV = "PLOBI_OFFLOAD_GEMINI_COMMAND"
GEMINI_EXTRA_ARGS_ENV = "PLOBI_OFFLOAD_GEMINI_EXTRA_ARGS"
GEMINI_YOLO_ENV = "PLOBI_OFFLOAD_GEMINI_YOLO"
OPENCODE_COMMAND_ENV = "PLOBI_OFFLOAD_OPENCODE_COMMAND"
OPENCODE_EXTRA_ARGS_ENV = "PLOBI_OFFLOAD_OPENCODE_EXTRA_ARGS"
#: ``opencode run --help`` 现核（v2.0.32）：``--auto`` = 自动批准未被显式拒绝的权限。
OPENCODE_AUTO_ENV = "PLOBI_OFFLOAD_OPENCODE_AUTO"

# ── 厂商的键 ────────────────────────────────────────────────────────────────

AGENT_CODEBUDDY = "codebuddy"
AGENT_AGY = "agy"
AGENT_QODER = "qoder"
#: 裁定 205：ACP 腿（工具在上游闭环执行、回来是文本）不进模型清单，归这一栏。
#: 它们本来就在本机装好、也已登录过 —— 外包这一栏才是它们该出现的地方。
AGENT_OPENCODE = "opencode"
AGENT_CLINE = "cline"
AGENT_GEMINI = "gemini"
#: 11-02 移出额度栏（bezel/config.yaml 不再注册这三条 spawn CLI 腿）：codex / claude
#: 归外包这一栏。提示词形状沿用 2026-10-25 接入时对它们各自 ``--help`` 的实测记录，
#: 外包通道多走的是权限旗标那一档。
AGENT_CODEX = "codex"
AGENT_CLAUDE = "claude"
#: 11-08 现核：这两家在这台都装着，而且都是**自己会跑工具的完整 agent**
#: （``kiro-cli --version`` = 2.35.1、``qwen --version`` = 0.26.0）⇒ 按裁定 205 的口径，
#: 它们该出现在外包这一栏（模型选择框那一侧走不了工具闭环的才是它们）。
AGENT_KIRO = "kiro"
AGENT_QWEN = "qwen"
#: 11-08 18:0x 实装并现核这两家（版本号是装完当场 ``--version`` 读到的）：
#: **Copilot CLI 1.0.107**（``@github/copilot``）与 **Kilo Code CLI 7.8.27**（``@kilocode/cli``，MIT）。
AGENT_COPILOT = "copilot"
AGENT_KILO = "kilo"

#: 这一家还没有实测过的回跳旗标时，写的就是这一句（**不许拿看起来像的名字顶上去**）。
NO_RESUME_FLAG = "这一家还没有实测过的回跳旗标"

#: argv 的默认排法。``{task}`` 是提示词，``{flags}``／``{extra}`` 是展开位，空的自动丢掉。
DEFAULT_ARGV_TEMPLATE = ("-p", "{task}", "--output-format", "json", "{flags}", "{extra}")


@dataclasses.dataclass(frozen=True)
class ExternalAgentSpec:
    """一家外部 AI 软件「怎么被起来」的结构性事实。

    为什么是一张表而不是 ``if agent == …`` 的梯子：第三家进来时，梯子要在
    ``resolve_command`` / ``permission_flags`` / ``extra_args`` 三个函数里
    各加一条分支，漏一处就是「这家悄悄用了那家的旗标」——CodeBuddy 的
    ``bypassPermissions`` 拼到 Qoder 上根本不是一个合法档位，正是这一类。
    差异收在一格里，公共层只认表。

    ⚠️ **字段与默认值一个字不许改**（公共形状）：屏面与工具面都照着它读。
    """

    name: str
    binary_name: str
    command_env: str
    extra_args_env: str
    permission_modes: tuple[str, ...] = ()
    default_permission_mode: str = ""
    permission_mode_env: str = ""
    #: ``eq`` → ``--permission-mode=x``（CodeBuddy）；``sep`` → ``--permission-mode x``（Qoder）。
    permission_shape: str = "eq"
    permission_prefix: tuple[str, ...] = ()
    #: 没有阶梯的那一家（agy）：只有一个开关，配置为真 ⇒ 给这一串旗标。
    skip_flag: tuple[str, ...] = ()
    skip_permissions_env: str = ""
    #: 这一家**官方那条登录命令**（空 = 没有能一键拉起的登录流程）。
    #: 只登记那种「跑起来自己开浏览器、人点一下同意就行」的；**需要终端交互的不登记**
    #: （kilo 的 ``auth login`` 要人挑 provider：后台起一个没有 TTY 的交互进程只会卡死）。
    login_argv: tuple[str, ...] = ()
    #: 把环境交给子进程之前要洗掉的变量（Qoder 见 :data:`QODER_SDK_ENV`）。
    scrub_env: tuple[str, ...] = ()
    #: 「exit 0 但没落盘 ⇒ 它其实没干活」这条判据**适用于哪几家**。
    #: CodeBuddy 与 agy 是实测出来的；Qoder 还没测过，而 10-22 现读它**读了文件并答对了内容**、
    #: 只是没写文件 —— 对它套这条判据会把一次成功的只读活判成失败。未测 ⇒ 不启用。
    artifact_required: bool = True
    #: argv 的排法。``{task}`` 是提示词，其余按序展开，空的自动丢掉。
    #: CodeBuddy / agy 是**实测跑通**的形状，一个字不改；Qoder 的提示词是变长位置参数
    #: （``[query...]``），写在它后面的选项会被吞进提示词本身 —— 10-22 实测：
    #: ``--output-format json`` 没生效（回包是散文）、``--permission-mode`` 也没生效
    #: （它想写文件时被权限层当场拒掉）。所以这一家必须把选项排在提示词**之前**。
    argv_template: tuple[str, ...] = DEFAULT_ARGV_TEMPLATE


@dataclasses.dataclass(frozen=True)
class VendorContract:
    """一家外部软件在**契约表**里的那一行。

    只收「散在别处、且别处会漂」的那几样事实：ACP 怎么起、回跳旗标、会话落在哪。
    通道怎么起、权限档、登录命令那些就在同一行（:class:`VendorRow`）里，**不重复**。
    """

    id: str
    #: 官方 ACP 注册表（``agentclientprotocol/registry``）里的 id。空 = 官方注册表里没有这一家。
    acp_registry_id: str = ""
    #: 起 ACP 模式的旗标（跟本机那个入口命令配着用）。空 = 没法用 ACP 问它。
    acp_args: tuple[str, ...] = ()
    #: 这一家派活走哪条腿（``cli``／``acp``，11-16）。
    #: ACP 腿的那几家**入口命令就是它自己**——旗标可以空（glm 那台 ACP server
    #: 不接旗标），所以「有没有 ACP 入口」不能只看旗标。
    dispatch_leg: str = "cli"
    #: 这一家 ACP 那一支**现核到哪一步**（给日志与 tooltip，不上屏）。
    acp_note: str = ""
    #: 回跳到它自己那条会话的旗标（``{session_id}`` 是唯一允许被替换的槽位）。
    resume_argv: tuple[str, ...] = ()
    #: 没实测过的那家，这里写明「这一家还没有实测过的回跳旗标」。
    resume_note: str = ""
    #: 它的会话在本机哪儿（``plobi/delegation/external_sessions.py`` 里那个读取器的键）。
    session_source: str = ""


@dataclasses.dataclass(frozen=True)
class PathRule:
    """落点规矩：候选从哪来，取哪一个。

    候选每条一种写法（``kind:value|rel``，``rel`` 是落在基目录下的相对路径）：

    ==================  ==================================================
    写法                 意思
    ==================  ==================================================
    ``env:NAME``        环境变量给基目录（没设就当这一条不存在）
    ``home:rel``        家目录下
    ``xdg:rel``         ``XDG_DATA_HOME`` 下
    ``localappdata:rel``  ``LOCALAPPDATA`` 下
    ``appdata:rel``     ``APPDATA`` 下
    ``literal:path``    字面路径（本机实测的老落点）
    ==================  ==================================================

    前面还可以加平台前缀（``darwin:``／``linux:``／``win32:``／``other:``＝非 darwin），
    不带前缀 = 哪台都行。

    取法（:attr:`pick`）：

    * ``first`` —— 第一个候选（环境变量指哪儿就是哪儿，哪怕那儿还没有）；
    * ``first-existing`` —— 第一个**存在**的候选；一个都没有就给第一个。
    """

    candidates: tuple[str, ...] = ()
    pick: str = "first"


@dataclasses.dataclass(frozen=True)
class AccountRule:
    """一"族"客席的**账号清单**从哪读（多账号体系的第一格，11-16）。

    账号清单是**盘上会变的东西**（用户随时切账号、加账号）⇒ 它不进真源表的数据，
    真源表里只放**读它的规矩**：文件在哪、数组键叫什么、哪个字段是账号标识、
    哪个字段是显示名、哪个字段标着"当前登录的是哪个"。

    **读不出名字的那一家也要能列出来**：厂商把昵称加密时（WorkBuddy 的
    ``{"$wbEncrypted": 1, "envelope": ...}``），显示名退到账号标识 ——
    宁可给一个认得出的标识，也不给空白。
    """

    #: 候选文件（``PathRule`` 那套写法：``kind:value|rel``）。
    files: tuple[str, ...] = ()
    #: 文件里那个数组的键（``accounts``／``allAccounts``……）。空 = 顶层就是数组。
    list_keys: tuple[str, ...] = ("accounts", "allAccounts")
    #: 账号标识取哪个字段（``uid``）。
    id_field: str = "uid"
    #: 显示名取哪个字段（``nickname``）。
    label_field: str = "nickname"
    #: 「当前登录的是哪个」取哪个字段（``lastLogin``）。
    current_field: str = "lastLogin"
    #: 名字被加密时的形状里那个标记（``{"$wbEncrypted": 1, ...}``）⇒ 读不出来。
    encrypted_marker: str = "$wbEncrypted"

    # ── 凭据交接（派活时把「哪一个身份」交给厂商）────────────────────────
    #: 厂商认哪个环境变量（**凭据交接的入口**）。空 = 这一族没有交接这一说。
    credential_env: str = ""
    #: 交接的形状：``session`` —— 厂商读的那份快照是
    #: ``{"session": {"account": …, "auth": …}}``（CodeBuddy CLI 现读）。
    credential_kind: str = ""
    #: 身份文件里「当前身份」那一块的键（``account``）。
    account_key: str = "account"
    #: 身份文件里「凭据」那一块的键（``auth``）。
    auth_key: str = "auth"


@dataclasses.dataclass(frozen=True)
class AcpModeMap:
    """ACP 那一腿的**档**怎么对（11-16：ACP 派活腿）。

    **档有两种性质，混不得**（11-15 本机现读三家）：

    * ``permission`` —— **权限档**：说的是「它准你做到哪一步」
      （GLM 三档：default／accept_edits／bypass_permissions；
      Claude 五档：default／acceptEdits／plan／auto／bypassPermissions）；
    * ``agent`` —— **agent 模式**：说的是「它是谁」
      （DevEco 四档：build／ask／goal／plan——干活的那个／问答的那个／出主意的那个）。

    把后者当权限档翻译就是**编**——把「它是谁」说成「它准你做到哪一步」，
    主就会以为派出去的活比实际更宽（或更窄）。⇒ 这一格是**表里声明**的，
    代码只按声明办事，**不按厂商分叉**（真源表一行一家，裁定 94 族）。
    """

    #: ``permission``（权限档）／``agent``（agent 模式）。空 = 这一家没有档。
    kind: str = ""
    #: 归一化档（``read_only``／``edit``／``full_auto``）→ 它那一档的 id。
    #: 成对写：先我们的，后它的。
    mapping: tuple[tuple[str, str], ...] = ()
    #: 不指定档时切到哪一档（空 = 不切，用它自己当前那档）。
    default: str = ""
    #: 一句人话（给日志与 tooltip，不上屏）。
    note: str = ""


@dataclasses.dataclass(frozen=True)
class FirstUserSpec:
    """「去另一张表取第一句用户话」那一跳的映射。

    Marvis 与 OpenCode 的会话名多半是人写的标题，屏上先看**人说的第一句**
    （跟别家同一形状），它自己起的名字另放一格（``source_title``），不丢信息。
    """

    table: str
    id_column: str
    role_column: str
    role_value: str
    order_column: str
    content_column: str
    #: 正文怎么取：``raw``（就是那一列）／``json-field``（那一列是一段 JSON，取 ``text_field``）。
    text_mode: str = "raw"
    text_field: str = ""
    #: 取不到那一列时是否跳过空值行（Marvis 的真库有 ``content is not null`` 那一句）。
    require_content: bool = False


@dataclasses.dataclass(frozen=True)
class SessionSpec:
    """一家外部软件的会话落在哪儿、字段怎么映射。

    **读法只有三种**（照实说：不是六家六套，是三种形状各带字段映射）：

    * ``jsonl-dir`` —— 目录里一堆 ``*.jsonl``，会话号＝文件名；
    * ``sqlite-rows`` —— 一张表若干列，可选「去消息表取第一句用户话」；
    * ``sqlite-kv-json`` —— 表头行 ＋ 一段 JSON 大块（Cursor 那种）。

    正文（transcript）另有一组形状：

    * ``jsonl-dialogue`` —— 一行一条，``type``／``role`` 分派（Qoder、CodeBuddy）；
    * ``jsonl-steps`` —— 一行一个 step，类型各映射一种角色（agy）；
    * ``sqlite-dialogue`` —— 一张消息表（Marvis、OpenCode）；
    * ``sqlite-kv-json`` —— 与会话同一个 KV 大块（Cursor）。
    """

    vendor: str
    #: 会话列表的读法形状。
    shape: str = ""
    #: 正文（transcript）的读法形状。
    transcript_shape: str = ""
    #: jsonl 的摆法：``project-dir``（``<根>/<项目>/<会话号>.jsonl``，Qoder 那一族）／
    #: ``flat``（``<根>/<会话号>.jsonl``，kiro 那一族：会话直接躺在根下，没有项目目录）。
    layout: str = "project-dir"
    #: jsonl 摆法里正文那层的**子目录**（gemini：``chats/``；其余为空）。
    jsonl_subdir: str = ""
    #: 这一家在「会话来源」那一列里的次序（照屏上现在的顺序；不加就排最后）。
    order: int = 0

    # ── 落在哪儿 ─────────────────────────────────────────────────────────
    #: 会话目录（``jsonl-dir`` 用）。
    root_rule: PathRule = dataclasses.field(default_factory=PathRule)
    #: 会话库文件（``sqlite-*`` 用）。
    db_rule: PathRule = dataclasses.field(default_factory=PathRule)
    #: 正文目录（``jsonl-steps`` 用；agy 的 ``brain/<会话号>/…``）。
    transcript_root_rule: PathRule = dataclasses.field(default_factory=PathRule)
    #: 落点解析器的名字（模块级那几只；它们读的就是上面那几条规矩）。
    #: 通用读取器按名字去调它们 ⇒ 别的线 monkeypatch 那个函数时依然生效。
    root_resolver: str = ""
    db_resolver: str = ""
    transcript_root_resolver: str = ""

    # ── 会话号从哪儿来 ───────────────────────────────────────────────────
    #: 会话号＝文件名（``jsonl-dir``）还是表里那一列（``sqlite-*``）——**由形状决定**，
    #: 这里不另设一格（免得两处能各说各话）。
    id_column: str = ""

    # ── 标题 ─────────────────────────────────────────────────────────────
    #: ``jsonl-role``（jsonl 那一族：取第一条那个角色的）／
    #: ``first-user``（去消息表取第一句）／``column``（表里那一列）。
    title_source: str = "column"
    #: jsonl 那一族：哪个角色的第一句算标题（Qoder／CodeBuddy 都是 ``user``）。
    title_role: str = "user"
    title_column: str = ""
    #: 标题列取不到时退到的那一列（agy 的 ``preview``）。
    title_fallback_column: str = ""
    #: 标题列怎么归一：``raw``（原样）／``first-line``（只留第一行）。
    title_norm: str = "raw"
    #: 第一句用户话那一跳（``title_source == "first-user"`` 时用）。
    first_user: Optional[FirstUserSpec] = None
    #: **厂商自起名的那条记录**（jsonl 那一族）：记录 ``type`` 要是它，
    #: 标题就从它的那一格取（CodeBuddy／WorkBuddy 的 ``ai-title`` 记录带 ``aiTitle``）。
    #: 空 = 不走这一跳（Qoder 那种：没有自起名的记录）。
    title_record_type: str = ""
    #: 上面那条记录里标题所在的那一格（``aiTitle``）。
    title_record_field: str = ""
    #: 取标题时剥掉的那层包裹（``<user_query>``）：厂商把**人说的那一句**裹在里面，
    #: 外面还压着一层系统提醒（``<system-reminder>``）⇒ 不剥就是一串机器话。
    title_strip_wrapper: str = ""
    #: 剥完那层包裹**取哪一边**：``inside``（人话在包裹里面：agy／cline）／
    #: ``outside``（人话在包裹**后面**：gemini——那一大段是上下文，人话才在它后头）。
    strip_wrapper_mode: str = "inside"
    #: 这些值**不算标题**（kiro 的 ``[user]``／``None`` 是占位：它自己没起出名字）
    #: ⇒ 换一个来源取（先退到 jsonl 里那句人话，再退到会话号）。
    title_placeholder_values: tuple[str, ...] = ()

    # ── 项目 / 工作目录 ─────────────────────────────────────────────────
    #: ``none``／``column``（表里那一列）／``json-uri-list``（一列 JSON 列表，取第一项解码）／
    #: ``jsonl-field``（jsonl 记录里那一格）／``jsonl-workspace``（jsonl 里 ``workspace-directories``）／
    #: ``workspace-json``（工作区那一列指的是 ``workspaceStorage/<id>/workspace.json``）。
    project_mode: str = "none"
    project_column: str = ""
    project_field: str = ""

    # ── 时间 ─────────────────────────────────────────────────────────────
    #: ``mtime``（文件修改时间）／``state-json``（会话目录里的 ``state.json``）／
    #: ``column-local``（本地时间 ISO）／``column-epoch-ms``（epoch 毫秒）。
    updated_mode: str = "mtime"
    updated_column: str = ""
    #: 时间那一列空着时退到这一列（Cursor 的 ``lastUpdatedAt`` 空了就看 ``createdAt``）。
    updated_fallback_column: str = ""

    # ── 表与过滤 ─────────────────────────────────────────────────────────
    #: 主表名（``sqlite-rows`` 用）。
    table: str = ""
    #: 排序（``sqlite-rows`` 用），照抄厂商自己的排序。
    order_by: str = ""
    #: 归档那一列（``sqlite-kv-json`` 用）。
    archived_column: str = ""
    #: 会话号要跳过的前缀（Cursor 的草稿）。
    skip_id_prefixes: tuple[str, ...] = ()
    #: 要跳过的会话号（Cursor 的 ``empty-state-draft``）。
    skip_ids: tuple[str, ...] = ()
    #: 另放一格 ``source_title``（Marvis、OpenCode）。
    emit_source_title: bool = False
    #: 能不能把话送进去：``never``／``has-project``（Qoder）／``capability``（问能力表）。
    sendable_mode: str = "never"
    #: 「这一家读不读得动」怎么说：``always``（目录型，扫不到只是没会话）／
    #: ``db-exists``（库在就算读得动）／``db-and-sessions``（库在**且**真有会话才算）。
    readable_mode: str = "always"
    #: 读不到时照实说的那一句（**不编机制、不说「它没有对话」**）。
    missing_reason: str = ""
    #: 库在但一条会话都没有时照实说的那一句。
    empty_reason: str = ""

    # ── jsonl 那一族怎么分派角色 ────────────────────────────────────────
    #: 记录里「类型」在哪一格（``type``／kiro 的 ``kind``）。
    record_kind_field: str = "type"
    #: 记录 ``type`` 要是这几个之一（空 = 不看 type）。
    accept_types: tuple[str, ...] = ()
    #: 记录 ``role`` 要是这几个之一（空 = 不看 role）。
    accept_roles: tuple[str, ...] = ()
    #: 角色是 ``type`` 自己（Qoder），还是 ``role`` 那一格（CodeBuddy）。
    role_from_type: bool = False
    #: 正文在那条记录的哪一格（``message.content``／``content``／kiro 的 ``data.content``）。
    content_path: str = "content"
    #: 取不到时退到哪一格。
    content_fallback: str = ""
    #: 内容块里「类型」在哪一格（kiro 的块用 ``kind`` 而不是 ``type``）。
    content_block_kind_field: str = "type"
    #: 内容块里正文在哪一格（kiro 的块把正文放在 ``data`` 里）。
    content_block_text_field: str = "text"
    #: 时间戳那一格。
    ts_field: str = ""
    #: 时间戳那一格的**点路径**（kiro：``data.meta.timestamp``）；空 = 用 ``ts_field`` 扁平取。
    ts_path: str = ""
    #: 时间怎么归一：``raw``（原样）／``epoch-s``（epoch **秒**）／``epoch-ms``／``local``（ISO→本地串）。
    ts_mode: str = "raw"

    # ── jsonl 那一族：会话号／项目／时间从哪来 ─────────────────────────
    #: **首记录**那一族：会话号从 jsonl 第一条记录的那一格取（gemini 的 ``sessionId``）；
    #: 空 = 会话号＝文件名。
    header_id_field: str = ""
    #: 时间从首记录的那一格取（gemini 的 ``lastUpdated``）；空 = 走 ``updated_mode``。
    header_updated_field: str = ""
    #: 工作目录从旁挂的**小文件**里取（gemini 的 ``.project_root``，一行一个路径）；
    #: 相对会话目录。
    project_file: str = ""

    # ── 旁挂元数据（sidecar）那一族 ─────────────────────────────────────
    #
    # 有些家把**会话本身的元数据**写在正文文件旁边的一个 JSON 里（kiro：
    # ``<会话号>.json`` 里有 title／cwd／updated_at，正文在同名 ``.jsonl`` 里）。
    #: 旁挂那个文件在哪（相对会话目录；``{session_id}`` 是会话号占位）。
    sidecar_glob: str = ""
    #: sidecar 里标题在哪一格（kiro：``title``）。
    sidecar_title_field: str = ""
    #: sidecar 里工作目录在哪一格（kiro：``cwd``）。
    sidecar_project_field: str = ""
    #: sidecar 里时间在哪一格（kiro：``updated_at``）。
    sidecar_updated_field: str = ""

    # ── 正文（transcript）怎么读 ────────────────────────────────────────
    #: jsonl 正文在会话目录下的相对位置（``*/<会话号>.jsonl``）。
    transcript_glob: str = ""
    #: jsonl 正文那一条的**类型 → 角色**（两种 jsonl 形状共用这一张：
    #: ``jsonl-steps`` 的 ``USER_INPUT``→user…／``jsonl-dialogue`` 的 kiro ``Prompt``→user…）。
    kind_roles: tuple[tuple[str, str], ...] = ()
    #: jsonl 正文里要剥掉的那层包裹（agy 的 ``<USER_REQUEST>``）。
    strip_wrapper: str = ""
    #: 上面那层只对哪个角色剥（agy 的是**人说的**那层，回复不剥）。
    strip_wrapper_roles: tuple[str, ...] = ("user",)
    #: jsonl 正文里这些内容当「没说话」（agy 的 ``None``）。
    skip_contents: tuple[str, ...] = ()
    #: 上面那一条只对哪个角色生效（agy 的 ``None`` 是回复里的，不是人说的）。
    skip_contents_roles: tuple[str, ...] = ("assistant",)
    #: 正文那张消息表（``sqlite-dialogue`` 用）。
    transcript_table: str = ""
    transcript_id_column: str = ""
    transcript_role_column: str = ""
    transcript_content_column: str = ""
    transcript_order_column: str = ""
    transcript_ts_column: str = ""
    # ── 「每会话一个目录、里面两个 JSON」那一族（cline）────────────────
    #: 那份元数据 JSON 的文件名（相对会话目录）。
    meta_glob: str = ""
    #: 元数据里会话号／标题／工作目录／时间各在哪一格。
    meta_id_field: str = ""
    meta_title_field: str = ""
    meta_project_field: str = ""
    meta_updated_field: str = ""
    #: 时间那一格空着时退到这一格（cline 的 ``ended_at`` 空了就看 ``started_at``）。
    meta_updated_fallback_field: str = ""
    #: 正文那份 JSON 的文件名（相对会话目录）。
    messages_glob: str = ""
    #: 正文里消息数组在哪一格；消息的 role／正文／时间各在哪一格。
    messages_field: str = ""
    message_role_field: str = ""
    message_content_field: str = ""
    message_ts_field: str = ""
    #: 消息时间怎么归一：``local``（ISO）／``epoch-ms``（epoch 毫秒）。
    message_ts_mode: str = "local"

    #: 正文那两张表（``sqlite-parts``：消息表 ＋ 块表）——kilo 把正文拆成两表：
    #: 消息表只说**谁说的**，正文在块表里（每块一行）。
    transcript_part_table: str = ""
    transcript_part_message_column: str = ""
    transcript_part_content_column: str = ""
    transcript_part_order_column: str = ""
    #: 消息表里**这条消息自己的 id**（块表靠它挂上去；kilo 是 ``id``）。
    transcript_message_id_column: str = ""
    #: 消息那一列是**一段 JSON**：角色在哪一格（kilo 的 ``data`` 里写着 ``role``）。
    transcript_data_column: str = ""
    transcript_role_field: str = ""

    #: **角色编在列名里**那一族（copilot 的 ``turns`` 表）：一行一个 turn，
    #: 人说的那一句与它答的那一句各占一列 ⇒ 两列名都写在这张表里。
    turn_user_column: str = ""
    turn_assistant_column: str = ""
    #: 正文的时间怎么归一：``local``／``epoch``。
    transcript_ts_mode: str = "local"
    #: 正文那一列怎么取字：``raw``／``json-field``（user）／``json-parts``（assistant）。
    user_text_mode: str = "raw"
    user_text_field: str = ""
    assistant_text_mode: str = "raw"
    #: 正文那张表要不要「内容不为空」那一句（Marvis 的真库有）。
    require_transcript_content: bool = False

    # ── KV 大块那一族（Cursor）──────────────────────────────────────────
    #: 表头表与 KV 表。
    headers_table: str = ""
    kv_table: str = ""
    #: KV 的键前缀（``composerData:``）。
    kv_key_prefix: str = ""
    #: 表头里工作区那一列。
    workspace_column: str = ""
    #: 工作区那一格在 ``workspaceStorage/<id>/workspace.json`` 里，取这几个键之一。
    workspace_json_keys: tuple[str, ...] = ()
    #: 大块里对话在 ``conversationMap``、顺序在 ``fullConversationHeadersOnly``。
    kv_map_key: str = ""
    kv_order_key: str = ""
    #: 气泡类型 → 角色。
    kv_role_types: tuple[tuple[Any, str], ...] = ()
    kv_text_keys: tuple[str, ...] = ()


@dataclasses.dataclass(frozen=True)
class InstallSpec:
    """一家**怎么装**（用户 11-15 13:0x：「假设用户的是全新的电脑。只下了我们的 Plobi。
    用户第一次点配置的『+』的时候。没有下载就引导用户下载相关软件并引导登录。」）。

    这一族事实是**给新用户的第一扇门**：它回答「怎么把它装上」，
    通道表（:class:`ExternalAgentSpec`）回答「装好之后怎么把活交给它」。
    两件事分开记，**不许把没实测过的派活事实混进来**。
    """

    #: 装它的那条命令（人话，可复制到终端跑）。空 = 这一家没有可复制的命令（只给官方页）。
    command: str = ""
    #: 包名（npm／pypi）—— 查得到版本的那种事实。
    package: str = ""
    #: 官方下载页（点开就是下载或下载说明）。
    homepage: str = ""
    #: 装完之后它的命令叫什么（人认得出来的那一个）。
    binary: str = ""
    #: 一句说明（有什么坑、为什么给这一条）。
    note: str = ""
    #: 这条安装事实**是不是现核过的**（11-15 现核：npm registry／PyPI／官网探活）。
    #: False ⇒ 必须带一句「照官方文档写的，没在本机实测过」。
    verified: bool = False


@dataclasses.dataclass(frozen=True)
class VendorRow:
    """**真源表的一行**：一家外部软件的全部「这一家特有」的事实。

    四张出口表都由这一行派生（见本模块顶部那张对照表）。
    加一家 provider ⇒ **只在这里加一行**。
    """

    id: str
    binary_name: str
    command_env: str
    extra_args_env: str

    #: 这一家**能不能派活**（进外包通道那一栏）。False = 只读它的会话，还没接进通道
    #: （Marvis、Cursor 就是这一档：它们有自己的会话，但没接成外包目标）。
    offloadable: bool = True

    # ── 装 ─────────────────────────────────────────────────────────────
    #: 这一家**怎么装**（新用户的第一扇门：点「+」时屏上给的就是它）。
    install: InstallSpec = dataclasses.field(default_factory=InstallSpec)

    # ── 通道 ─────────────────────────────────────────────────────────────
    permission_modes: tuple[str, ...] = ()
    default_permission_mode: str = ""
    permission_mode_env: str = ""
    permission_shape: str = "eq"
    permission_prefix: tuple[str, ...] = ()
    skip_flag: tuple[str, ...] = ()
    skip_permissions_env: str = ""
    login_argv: tuple[str, ...] = ()
    scrub_env: tuple[str, ...] = ()
    artifact_required: bool = True
    argv_template: tuple[str, ...] = DEFAULT_ARGV_TEMPLATE
    #: 这台机器上它住在便携目录、不在 PATH 时的那条落点（别处没有就当没装）。
    portable_fallback: str = ""
    #: 厂商自己那条「我登没登」的探针（列模型／doctor 那一类；空 = 没登记）。
    login_probe: tuple[str, ...] = ()

    # ── 登录 ─────────────────────────────────────────────────────────────
    #: 本地凭据落点（只探在不在，**不读内容**）。空 = 没登记 ⇒ 探不到，不判没登录。
    login_hints: tuple[str, ...] = ()

    # ── ACP ──────────────────────────────────────────────────────────────
    acp_registry_id: str = ""
    acp_args: tuple[str, ...] = ()
    acp_note: str = ""
    #: 这一家派活**走哪条腿**：``cli``（命令行）／``acp``（协议）。
    #: 纯 ACP 客席只有后者——它们没有一条跑通的命令行腿（11-16 派活腿）。
    dispatch_leg: str = "cli"
    #: ACP 那一腿的**档**怎么对（权限档／agent 模式；表里声明，代码不按厂商分叉）。
    acp_modes: Optional[AcpModeMap] = None

    # ── 回跳 ─────────────────────────────────────────────────────────────
    resume_argv: tuple[str, ...] = ()
    resume_note: str = ""

    # ── 会话 ─────────────────────────────────────────────────────────────
    session: Optional[SessionSpec] = None

    # ── 多账号 ───────────────────────────────────────────────────────────
    #: 这一族的**账号清单**从哪读（规矩在表里，账号在盘上）。空 = 这一族只有一种身份。
    accounts_rule: Optional[AccountRule] = None

    # ── 派生 ─────────────────────────────────────────────────────────────
    def to_spec(self) -> ExternalAgentSpec:
        """这一行在**通道表**里的形状（字段与默认值照旧）。"""
        return ExternalAgentSpec(
            name=self.id,
            binary_name=self.binary_name,
            command_env=self.command_env,
            extra_args_env=self.extra_args_env,
            permission_modes=self.permission_modes,
            default_permission_mode=self.default_permission_mode,
            permission_mode_env=self.permission_mode_env,
            permission_shape=self.permission_shape,
            permission_prefix=self.permission_prefix,
            skip_flag=self.skip_flag,
            skip_permissions_env=self.skip_permissions_env,
            login_argv=self.login_argv,
            scrub_env=self.scrub_env,
            artifact_required=self.artifact_required,
            argv_template=self.argv_template,
        )

    def to_contract(self) -> VendorContract:
        """这一行在**契约表**里的形状。"""
        return VendorContract(
            id=self.id,
            acp_registry_id=self.acp_registry_id,
            acp_args=self.acp_args,
            acp_note=self.acp_note,
            dispatch_leg=self.dispatch_leg,
            resume_argv=self.resume_argv,
            resume_note=self.resume_note,
            session_source=self.session.vendor if self.session else "",
        )


# ── 真源表本体：一行一家 ───────────────────────────────────────────────────
#
# 顺序 = 屏上名册的顺序（``plobi/delegation/offload_surface.py`` 照 ``EXTERNAL_AGENTS``
# 的次序列出来），**不要随手重排**。

VENDOR_ROWS: tuple[VendorRow, ...] = (
    VendorRow(
        id=AGENT_CODEBUDDY,
        install=InstallSpec(command="npm i -g @tencent-ai/codebuddy-code", package="@tencent-ai/codebuddy-code", homepage="https://cnb.cool/codebuddy/codebuddy-code", binary="codebuddy", verified=True),
        binary_name="codebuddy",
        command_env=COMMAND_ENV,
        extra_args_env=EXTRA_ARGS_ENV,
        permission_modes=PERMISSION_MODES,
        default_permission_mode=DEFAULT_PERMISSION_MODE,
        permission_mode_env=PERMISSION_MODE_ENV,
        permission_prefix=("-y",),
        login_probe=("doctor",),
        login_hints=("~/.codebuddy/local_storage",),
        # 11-16 现读：这一族的**账号清单**落在三个身份文件里（各有 accounts／allAccounts 数组）；
        # 盘上那两个账号目录（``<CODEBUDDY_CONFIG_DIR>/<uid>/``）只装 UI 快照，**不装会话**。
        accounts_rule=AccountRule(
            files=(
                "localappdata:LOCALAPPDATA|CodeBuddyExtension/Data/Public/auth/Tencent-Cloud.coding-copilot.info",
                "localappdata:LOCALAPPDATA|CodeBuddyExtension/Data/Public/auth/workbuddy-desktop.info",
                "localappdata:LOCALAPPDATA|CodeBuddyExtension/Data/Public/auth/workbuddy-desktop-ai.info",
            ),
            # 11-16 现读厂商包：非交互的派活腿吃不下它自己那条交互式切换
            # （`/login`＝「Switch Tencent Cloud CodeBuddy accounts」，底层是拿 refresh token
            # 打 `/v2/…/account/switch`）⇒ 走它给非交互进程留的那个入口：
            # 一个**凭据快照文件**的路径（`acceptSession` 要 `account` + `auth.accessToken`）。
            credential_env="CODEBUDDY_AUTH_SNAPSHOT_PATH",
            credential_kind="session",
        ),
        acp_registry_id="codebuddy-code",
        acp_args=("--acp",),
        acp_note="11-14 现核：给了会话（登录着）",
        resume_argv=("--resume", "{session_id}", "--open"),
        session=SessionSpec(
            vendor=AGENT_CODEBUDDY,
            shape="jsonl-dir",
            transcript_shape="jsonl-dialogue",
            order=2,
            root_rule=PathRule(
                candidates=("env:CODEBUDDY_CONFIG_DIR|projects", "home:.codebuddy|projects"),
                pick="first",
            ),
            root_resolver="codebuddy_root",
            title_source="jsonl-role",
            title_role="user",
            # 11-15 现读（这台机器上那 103 条会话）：厂商自己在 jsonl 里留了一条
            # ``ai-title`` 记录（``aiTitle``＝「解决卸载ikuuu后marvis网络问题」）⇒
            # 标题优先取它自己起的名字；取不到才退到第一条用户话，
            # 而那条话裹着 ``<user_query>``，外面还压着 ``<system-reminder>``
            # ⇒ 不剥就是一句机器话（屏上原来显示的就是那个）。
            title_record_type="ai-title",
            title_record_field="aiTitle",
            title_strip_wrapper="user_query",
            project_mode="jsonl-field",
            project_field="cwd",
            updated_mode="mtime",
            accept_types=("message",),
            accept_roles=("user", "assistant"),
            role_from_type=False,
            content_path="content",
            ts_field="timestamp",
            transcript_glob="*/{session_id}.jsonl",
            sendable_mode="never",
        ),
    ),
    VendorRow(
        id=AGENT_AGY,
        install=InstallSpec(
            command="winget install --id Google.AntigravityCLI",
            package="",
            homepage="https://antigravity.google/docs",
            binary="agy",
            verified=True,
            note="官方安装走 winget（Google.AntigravityCLI；11-15 在 winget 索引里现核）；这台机器上装的是便携目录里的那一份",
        ),
        binary_name="agy",
        command_env=AGY_COMMAND_ENV,
        extra_args_env=AGY_EXTRA_ARGS_ENV,
        skip_flag=("--dangerously-skip-permissions",),
        skip_permissions_env=AGY_SKIP_ENV,
        login_probe=("models",),
        # 11-08 21:5x 实测：**光有文件 ≠ 登上了**。
        # - agy 的令牌结构是 ``{token: {access_token, refresh_token, expiry, ...}, auth_method, id_token}``，
        #   ``token.expiry`` = 2026-11-05T17:59:05Z（**已过期**）⇒ 真跑回 ``Authentication required``。
        #   ⇒ 这一条要**读那一个过期字段**：过期就是没登录。
        login_hints=("json-expiry:~/.gemini/antigravity-cli/antigravity-oauth-token#token.expiry",),
        acp_registry_id="antigravity-acp",
        acp_args=("acp",),
        acp_note="11-14 现核：没回话 ⇒ 探不到（官方注册表里它是二进制形态，本机这一支的旗标没试出来）",
        resume_argv=("--conversation", "{session_id}"),
        session=SessionSpec(
            vendor=AGENT_AGY,
            shape="sqlite-rows",
            transcript_shape="jsonl-steps",
            order=5,
            db_rule=PathRule(
                candidates=("env:AGY_HOME|conversation_summaries.db",
                            "home:.gemini/antigravity-cli|conversation_summaries.db"),
                pick="first",
            ),
            db_resolver="agy_db",
            transcript_root_rule=PathRule(
                candidates=("env:AGY_HOME|brain", "home:.gemini/antigravity-cli|brain"),
                pick="first",
            ),
            transcript_root_resolver="agy_brain_root",
            readable_mode="db-exists",
            missing_reason="没找到 agy 的会话摘要库",
            id_column="conversation_id",
            title_source="column",
            title_column="title",
            title_fallback_column="preview",
            title_norm="raw",
            project_mode="json-uri-list",
            project_column="workspace_uris",
            updated_mode="column-local",
            updated_column="last_modified_time",
            table="conversation_summaries",
            order_by="last_modified_time desc",
            kind_roles=(("USER_INPUT", "user"), ("PLANNER_RESPONSE", "assistant")),
            strip_wrapper="<USER_REQUEST>",
            skip_contents=("None",),
            transcript_glob="{session_id}/.system_generated/logs/transcript.jsonl",
            sendable_mode="never",
        ),
    ),
    # ── 只读会话、还没接进外包通道的那两家 ────────────────────────────
    #
    # 它们**有**会话可读（屏上「外部会话」那一栏要看），但**不是**外包目标 ⇒
    # ``offloadable=False`` ⇒ 不进通道表、不进契约表，只进会话那张表。

    VendorRow(
        id="marvis",
        install=InstallSpec(command="", homepage="", binary="marvis", verified=False, note="桌面版软件，没有命令行安装包 ⇒ 这一格不给安装引导"),
        binary_name="marvis",
        command_env="",
        extra_args_env="",
        offloadable=False,
        session=SessionSpec(
            vendor="marvis",
            shape="sqlite-rows",
            transcript_shape="sqlite-dialogue",
            order=3,
            db_rule=PathRule(
                candidates=("env:MARVIS_HOME|database/data.db", "home:.marvis|database/data.db"),
                pick="first",
            ),
            db_resolver="marvis_db",
            id_column="conversation_id",
            # Marvis 的会话名多半是**项目名**（Plobi／SRTP／Quota），不是人说的第一句话。
            # 屏上先看第一句话（跟别家同一形状），项目名另放一格（``source_title``），不丢信息。
            title_source="first-user",
            title_column="title",
            title_norm="raw",
            first_user=FirstUserSpec(
                table="messages",
                id_column="conversation_id",
                role_column="role",
                role_value="user",
                order_column="message_seq",
                content_column="content",
                text_mode="raw",
                require_content=True,
            ),
            # Marvis **不记这条会话在哪个目录**（11-08 现读：库里的 metadata、工具调用参数、
            # AppData 下那圈目录，三处都没有工作目录这一格）⇒ 留空，归属交给 L2 按标题判。
            # **不从产物名反推**——那是猜，猜出来的目录会把「有关/无关」判反。
            project_mode="none",
            updated_mode="column-local",
            updated_column="updated_at",
            table="conversations",
            order_by="updated_at desc",
            emit_source_title=True,
            transcript_table="messages",
            transcript_id_column="conversation_id",
            transcript_role_column="role",
            transcript_content_column="content",
            transcript_order_column="message_seq",
            transcript_ts_column="created_at",
            transcript_ts_mode="local",
            require_transcript_content=True,
            sendable_mode="never",
        ),
    ),
    VendorRow(
        id="cursor",
        install=InstallSpec(command="irm 'https://cursor.com/install?win32=true' | iex", homepage="https://cursor.com/docs/cli/overview", binary="agent", verified=True, note="Cursor CLI 装完的命令叫 agent（官方文档现读）；这台机器上只有编辑器，那个命令不叫 agent；这一家还没接派活腿"),
        binary_name="cursor",
        command_env="",
        extra_args_env="",
        offloadable=False,
        session=SessionSpec(
            vendor="cursor",
            shape="sqlite-kv-json",
            transcript_shape="sqlite-kv-json",
            order=4,
            # Cursor 的 composer 存在 VS Code 那套 ``state.vscdb`` 里（只读打开）。
            # ``CURSOR_HOME`` 指哪儿就是哪儿（哪怕那儿还没有）；没指才按规矩去找。
            db_rule=PathRule(
                candidates=(
                    "env:CURSOR_HOME|User/globalStorage/state.vscdb",
                    "localappdata:Cursor|User/globalStorage/state.vscdb",
                    "appdata:Cursor|User/globalStorage/state.vscdb",
                    # ⚠ 这一条是**写死的盘符**（本机实测的老落点）——欠账，别当范例抄。
                    "literal:D:/Data/AppData/Cursor|User/globalStorage/state.vscdb",
                    "home:.cursor|User/globalStorage/state.vscdb",
                ),
                pick="env-or-first-existing",
            ),
            db_resolver="cursor_db",
            readable_mode="db-and-sessions",
            missing_reason="没找到 Cursor 的 state.vscdb",
            empty_reason="Cursor 这台机器上没留下可读的对话",
            id_column="composerId",
            title_source="first-user",
            updated_mode="column-epoch-ms",
            updated_column="lastUpdatedAt",
            updated_fallback_column="createdAt",
            table="composerHeaders",
            archived_column="isArchived",
            # 草稿不是会话（这台机器上那六条全是草稿或已归档）。
            skip_id_prefixes=("draft-",),
            skip_ids=("empty-state-draft",),
            workspace_column="workspaceId",
            workspace_json_keys=("folder", "workspace", "configuration"),
            headers_table="composerHeaders",
            kv_table="cursorDiskKV",
            kv_key_prefix="composerData:",
            kv_map_key="conversationMap",
            kv_order_key="fullConversationHeadersOnly",
            kv_role_types=((1, "user"), ("user", "user"), (2, "assistant"), ("assistant", "assistant")),
            kv_text_keys=("text", "richText"),
            sendable_mode="never",
        ),
    ),

    VendorRow(
        id=AGENT_CLINE,
        install=InstallSpec(command="npm i -g cline", package="cline", homepage="https://cline.bot", binary="cline", verified=True),
        binary_name="cline",
        command_env=CLINE_COMMAND_ENV,
        extra_args_env=CLINE_EXTRA_ARGS_ENV,
        # 现核 v：``cline [options] [command] [prompt]``，提示词是位置参数，``--json`` 出机器读的流，
        # ``-y/--yolo`` 自动批准"有限的"工具集（厂商自己的措辞，别读成全放开）。
        skip_flag=("-y",),
        skip_permissions_env=CLINE_YOLO_ENV,
        artifact_required=False,
        argv_template=("--json", "{flags}", "{extra}", "{task}"),
        login_hints=("~/.cline/data/settings/providers.json",),
        acp_registry_id="cline",
        acp_args=("--acp",),
        acp_note="11-14 现核：给了会话（登录着）",
        resume_note=NO_RESUME_FLAG,
        # ── 会话腿（11-15 现读）─────────────────────────────────────────
        # ``~/.cline/data/sessions/<会话号>/``：**每个会话一个目录**，
        # 里面两份 JSON——``<会话号>.json``（元数据：cwd／起止时间／prompt）
        # ＋ ``<会话号>.messages.json``（消息数组）。
        session=SessionSpec(
            vendor=AGENT_CLINE,
            shape="json-session-dir",
            transcript_shape="json-messages",
            order=12,
            root_rule=PathRule(
                candidates=("home:.cline|data/sessions",),
                pick="first",
            ),
            root_resolver="cline_root",
            meta_glob="*.json",
            meta_id_field="session_id",
            meta_title_field="prompt",
            meta_project_field="cwd",
            meta_updated_field="ended_at",
            meta_updated_fallback_field="started_at",
            messages_glob="*.messages.json",
            messages_field="messages",
            message_role_field="role",
            message_content_field="content",
            message_ts_field="ts",
            user_text_mode="block-list",
            assistant_text_mode="block-list",
            message_ts_mode="epoch-ms",
            title_strip_wrapper="user_input",
            strip_wrapper="user_input",
            strip_wrapper_roles=("user",),
            sendable_mode="never",
        ),
    ),
    VendorRow(
        id=AGENT_GEMINI,
        install=InstallSpec(command="npm i -g @google/gemini-cli", package="@google/gemini-cli", homepage="https://github.com/google-gemini/gemini-cli", binary="gemini", verified=True),
        binary_name="gemini",
        command_env=GEMINI_COMMAND_ENV,
        extra_args_env=GEMINI_EXTRA_ARGS_ENV,
        # 现核：``-p/--prompt`` 才是非交互；不加它就是交互 CLI（会把子进程挂住）。
        skip_flag=("-y",),
        skip_permissions_env=GEMINI_YOLO_ENV,
        artifact_required=False,
        # ``--skip-trust``（现核 11-08 21:5x：它自己的 ``--help`` 写明
        # 「Trust the current workspace for this session」）——**只有 gemini 有这枚旗标**：
        # 不加它，它在非受信目录里会回
        # ``Approval mode overridden to "default" because the current folder is not trusted``，
        # 于是我们写的放开档位被它自己按回去（11-08 真跑实测）。
        # ⚠ ``-p`` 后面**只能跟提示词本身**：gemini 回 ``Cannot use both a positional prompt
        # and the --prompt (-p) flag together`` ⇒ 提示词不许再当位置参数补一遍。
        argv_template=("-p", "{task}", "--skip-trust", "{flags}", "{extra}"),
        # gemini：``~/.gemini/oauth_creds.json`` **是另一个产品（Antigravity）的凭据**，
        #   Gemini CLI 自己不认它（11-08 真跑回 ``Please set an Auth method in your ...settings.json
        #   or specify one of the following environment variables ...: GEMINI_API_KEY,
        #   GOOGLE_GENAI_USE_VERTEXAI, GOOGLE_GENAI_USE_GCA``）⇒ 留着它就是那一句假话。
        #   Gemini CLI 自己的落点是 ``~/.gemini/settings.json`` 的 ``auth.selectedType``
        #   （官方：``oauth-personal``）或那三个环境变量之一。
        login_hints=(
            "json-field:~/.gemini/settings.json#auth.selectedType=oauth-personal",
            "json-field:~/.gemini/settings.json#auth.selectedType=gemini-api-key",
            "env:GEMINI_API_KEY",
            "env:GOOGLE_GENAI_USE_VERTEXAI",
            "env:GOOGLE_GENAI_USE_GCA",
        ),
        acp_registry_id="gemini",
        acp_args=("--acp",),
        acp_note="11-14 现核：拒了，原话是「Gemini API key is missing or not configured.」"
                 "——不是认证类措辞 ⇒ 探不到，不判「没登录」",
        resume_note=NO_RESUME_FLAG,
        # ── 会话腿（11-15 现读）─────────────────────────────────────────
        # ``~/.gemini/tmp/<项目>/chats/session-<时间>-<短号>.jsonl``：
        # **会话号在首记录里**（``sessionId``），文件名只是 ``session-<时间>-<短号>``；
        # 工作目录在旁挂那个小文件里（``.project_root``，一行一个路径）；
        # 人说的那一句裹在 ``<session_context>`` 里 ⇒ 不剥就是一段机器话。
        session=SessionSpec(
            vendor=AGENT_GEMINI,
            shape="jsonl-dir",
            transcript_shape="jsonl-dialogue",
            order=10,
            jsonl_subdir="chats",
            root_rule=PathRule(
                candidates=("home:.gemini|tmp",),
                pick="first",
            ),
            root_resolver="gemini_root",
            header_id_field="sessionId",
            header_updated_field="lastUpdated",
            project_file=".project_root",
            title_source="jsonl-role",
            title_role="user",
            title_strip_wrapper="session_context",
            strip_wrapper_mode="outside",
            # 人话在那一大段上下文**之后** ⇒ 正文也按同一口径剥。
            strip_wrapper="session_context",
            strip_wrapper_roles=("user",),
            project_mode="none",
            updated_mode="mtime",
            accept_types=("user",),
            role_from_type=True,
            content_path="content",
            ts_field="timestamp",
            transcript_glob="*/chats/session-*-{session_id8}.jsonl",
            sendable_mode="never",
        ),
    ),
    VendorRow(
        id=AGENT_OPENCODE,
        install=InstallSpec(command="", homepage="https://opencode.ai/docs", binary="opencode", verified=True, note="官方安装脚本（见官网）。这台机器上是 v2.0.32 的独立构建，npm 上的 opencode-ai 停在 1.19.5 ⇒ 版本对不上，所以不给 npm 那条"),
        binary_name="opencode",
        command_env=OPENCODE_COMMAND_ENV,
        extra_args_env=OPENCODE_EXTRA_ARGS_ENV,
        # ``run [flags] [<message...>]``：消息是变长位置参数，旗标必须排在它前面。
        skip_flag=("--auto",),
        skip_permissions_env=OPENCODE_AUTO_ENV,
        # 没测过它是否"exit 0 但没落盘"，所以不套那条判据（与 Qoder 同理）。
        artifact_required=False,
        argv_template=("run", "--format", "json", "{flags}", "{extra}", "{task}"),
        # 这台机器上它住在便携目录、不在 PATH（``D:\_POLICY`` 的 Portables 约定）；
        # 别处没有这个文件时 is_file() 为假，等价于没装。
        portable_fallback="D:/Portables/cli/opencode/opencode.exe",
        acp_registry_id="opencode",
        acp_args=("acp",),
        acp_note="11-14 现核：给了会话（登录着）",
        resume_note=NO_RESUME_FLAG,
        session=SessionSpec(
            vendor=AGENT_OPENCODE,
            shape="sqlite-rows",
            transcript_shape="sqlite-dialogue",
            order=6,
            # 路径不写死（用户 11-09：「每个用户安装的应用的位置可是不一样的」）：
            # 候选顺序＝环境变量 override → ``XDG_DATA_HOME`` 规矩 → 各平台默认，取**第一个存在的**。
            # 找不到就交回第一个候选（调用方按「没有」处理），不编一个路径出来。
            db_rule=PathRule(
                candidates=(
                    "env:OPENCODE_HOME|opencode.db",
                    "xdg:XDG_DATA_HOME|opencode/opencode.db",
                    "darwin:home:Library/Application Support/opencode|opencode.db",
                    "other:home:.local/share/opencode|opencode.db",
                ),
                pick="first-existing",
            ),
            db_resolver="opencode_db",
            id_column="id",
            title_source="first-user",
            title_column="title",
            title_norm="first-line",
            first_user=FirstUserSpec(
                table="session_message",
                id_column="session_id",
                role_column="type",
                role_value="user",
                order_column="seq",
                content_column="data",
                text_mode="json-field",
                text_field="text",
            ),
            project_mode="column",
            project_column="directory",
            updated_mode="column-epoch-ms",
            updated_column="time_updated",
            table="session_v2",
            order_by="time_updated desc",
            emit_source_title=True,
            readable_mode="db-and-sessions",
            missing_reason="没找到 OpenCode 的会话库",
            empty_reason="OpenCode 这台机器上没留下可读的对话",
            transcript_table="session_message",
            transcript_id_column="session_id",
            transcript_role_column="type",
            transcript_content_column="data",
            transcript_order_column="seq",
            transcript_ts_column="time_created",
            transcript_ts_mode="epoch",
            user_text_mode="json-field",
            user_text_field="text",
            assistant_text_mode="json-parts",
            sendable_mode="capability",
        ),
    ),
    VendorRow(
        id=AGENT_CODEX,
        install=InstallSpec(command="npm i -g @openai/codex", package="@openai/codex", homepage="https://github.com/openai/codex", binary="codex", verified=True),
        binary_name="codex",
        command_env=CODEX_COMMAND_ENV,
        extra_args_env=CODEX_EXTRA_ARGS_ENV,
        # ``exec [flags] [prompt]``：提示词是位置参数；``--json`` 出机器读的流，
        # ``--skip-git-repo-check`` 让它在非 git 的私有 cwd 也能跑。外包要写产物，
        # 所以不沿用网关那格的 ``--sandbox read-only``。
        skip_flag=("--full-auto",),
        skip_permissions_env=CODEX_AUTO_ENV,
        # 没测过它是否"exit 0 但没落盘"，不套那条判据（与 Qoder 同理）。
        artifact_required=False,
        argv_template=("exec", "--json", "--skip-git-repo-check", "{flags}", "{extra}", "{task}"),
        acp_note="官方 ACP 注册表里没有这一家 ⇒ 没法用 ACP 问它，登录态只能靠别的证据",
        resume_note=NO_RESUME_FLAG,
    ),
    VendorRow(
        id=AGENT_CLAUDE,
        install=InstallSpec(command="npm i -g @anthropic-ai/claude-code", package="@anthropic-ai/claude-code", homepage="https://github.com/anthropics/claude-code", binary="claude", verified=True),
        binary_name="claude",
        command_env=CLAUDE_COMMAND_ENV,
        extra_args_env=CLAUDE_EXTRA_ARGS_ENV,
        # ``claude -p "prompt" --output-format json``：与默认 argv_template 一致，不另写。
        skip_flag=("--dangerously-skip-permissions",),
        skip_permissions_env=CLAUDE_SKIP_ENV,
        # 没测过它是否"exit 0 但没落盘"，不套那条判据（与 Qoder 同理）。
        artifact_required=False,
        acp_note="官方 ACP 注册表里没有这一家 ⇒ 没法用 ACP 问它，登录态只能靠别的证据",
        resume_note=NO_RESUME_FLAG,
    ),
    VendorRow(
        id=AGENT_QODER,
        install=InstallSpec(
            command="npm i -g @qoder-ai/qodercli",
            package="@qoder-ai/qodercli",
            homepage="https://qoder.com/cli",
            binary="qodercli",
            verified=True,
            note="node 全局包（11-15 在 npm registry 现核：1.1.89）",
        ),
        binary_name="qodercli",
        command_env=QODER_COMMAND_ENV,
        extra_args_env=QODER_EXTRA_ARGS_ENV,
        permission_modes=QODER_PERMISSION_MODES,
        default_permission_mode=DEFAULT_QODER_PERMISSION_MODE,
        permission_mode_env=QODER_PERMISSION_MODE_ENV,
        permission_shape="sep",
        scrub_env=QODER_SDK_ENV,
        artifact_required=False,
        argv_template=("{flags}", "--output-format", "json", "{extra}", "-p", "{task}"),
        login_probe=("--list-models",),
        login_hints=("~/.qoder/.auth",),
        acp_registry_id="qoder",
        acp_args=("--acp",),
        acp_note="11-14 现核：12s 没回话（它起得慢）⇒ 探不到",
        resume_argv=("--resume", "{session_id}"),
        session=SessionSpec(
            vendor=AGENT_QODER,
            shape="jsonl-dir",
            transcript_shape="jsonl-dialogue",
            order=1,
            root_rule=PathRule(
                candidates=("env:QODER_CONFIG_DIR|projects", "home:.qoder|projects"),
                pick="first",
            ),
            root_resolver="qoder_root",
            title_source="jsonl-role",
            title_role="user",
            project_mode="jsonl-workspace",
            updated_mode="state-json",
            accept_types=("user", "assistant"),
            role_from_type=True,
            content_path="message.content",
            content_fallback="content",
            ts_field="timestamp",
            transcript_glob="*/{session_id}.jsonl",
            sendable_mode="has-project",
        ),
    ),
    VendorRow(
        id="qoder-cn",
        # 11-15 现读：这台机器上装的是 **Qoder 中国版**——它自己的
        # ``.qoder-app-status.json`` 写的是 ``product: "qodercn"``、账号是另一个
        # （邓德彧）、二进制是 ``qodercn.cmd``、会话 11 条与 qoder 那边零重叠
        # ⇒ **另一件安装**（同一产品的另一分区），单列一家，不并进 qoder。
        # 派活腿（怎么起它、权限档）本刀**没实测过** ⇒ ``offloadable=False``，
        # 只进会话表，不进通道表、不进契约表。
        install=InstallSpec(command="", homepage="", binary="qodercn", verified=False,
                            note="Qoder 中国版（这台机器上是 qodercn.cmd）；官方安装包没核到 ⇒ 不给一条可能是错的命令"),
        binary_name="qodercn",
        command_env="",
        extra_args_env="",
        offloadable=False,
        session=SessionSpec(
            vendor="qoder-cn",
            shape="jsonl-dir",
            transcript_shape="jsonl-dialogue",
            order=9,
            root_rule=PathRule(
                candidates=("home:.qoder-cn|projects",),
                pick="first",
            ),
            root_resolver="qoder_cn_root",
            title_source="jsonl-role",
            title_role="user",
            project_mode="jsonl-workspace",
            updated_mode="state-json",
            accept_types=("user", "assistant"),
            role_from_type=True,
            content_path="message.content",
            content_fallback="content",
            ts_field="timestamp",
            transcript_glob="*/{session_id}.jsonl",
            sendable_mode="never",
        ),
    ),
    VendorRow(
        id=AGENT_KIRO,
        install=InstallSpec(command="", homepage="https://kiro.dev/docs", binary="kiro-cli", verified=True, note="官方安装器（**不在 npm**：registry 上的 kiro-cli 是别的东西，命令叫 kirox）"),
        binary_name="kiro-cli",
        command_env=KIRO_COMMAND_ENV,
        extra_args_env=KIRO_EXTRA_ARGS_ENV,
        # ``kiro-cli chat [OPTIONS] [INPUT]``：``--no-interactive`` 一次性（不进 TUI），
        # ``-a/--trust-all-tools`` 是它自己那一枚放开开关（不是 cline 的 ``-y``，也不是 opencode 的 ``--auto``）。
        # 它的 ``--output-format`` 帮助里写明**只作用于 list 命令** ⇒ 不传给 chat：回包按文本读，
        # 金额保持"未知"（None）而不是编一个 0。
        skip_flag=("--trust-all-tools",),
        skip_permissions_env=KIRO_TRUST_ENV,
        # 没测过它是否"exit 0 但没落盘"，不套那条判据（与 Qoder 同理）。
        artifact_required=False,
        argv_template=("chat", "--no-interactive", "{flags}", "{extra}", "{task}"),
        acp_registry_id="kiro",
        acp_args=("acp",),
        acp_note="11-14 现核：给了会话（登录着）",
        resume_note=NO_RESUME_FLAG,
        # ── 会话腿（11-15 现读）─────────────────────────────────────────
        # ``~/.kiro/sessions/cli/<会话号>.json`` ＋同名 ``.jsonl``：
        # **元数据在旁挂那个 .json 里**（title／cwd／updated_at），正文在 .jsonl 里；
        # 记录判别键是 ``kind``（``Prompt``／``AssistantMessage``），
        # 正文块是 ``{"kind":"text","data":…}``，时间是 ``data.meta.timestamp``（epoch **秒**）。
        # ⇒ 通用旋钮：layout=flat／sidecar_*／record_kind_field／content_block_*／ts_path。
        session=SessionSpec(
            vendor=AGENT_KIRO,
            shape="jsonl-dir",
            transcript_shape="jsonl-dialogue",
            order=7,
            layout="flat",
            root_rule=PathRule(
                candidates=("home:.kiro|sessions/cli",),
                pick="first",
            ),
            root_resolver="kiro_root",
            sidecar_glob="{session_id}.json",
            sidecar_title_field="title",
            sidecar_project_field="cwd",
            sidecar_updated_field="updated_at",
            # 它自己没起出名字时占位写 ``[user]``／``None`` ⇒ 不算标题，退到 jsonl 那句人话。
            title_placeholder_values=("[user]", "None"),
            title_source="jsonl-role",
            title_role="user",
            project_mode="jsonl-field",
            project_field="cwd",
            updated_mode="mtime",
            record_kind_field="kind",
            kind_roles=(("Prompt", "user"), ("AssistantMessage", "assistant")),
            content_path="data.content",
            content_block_kind_field="kind",
            content_block_text_field="data",
            ts_path="data.meta.timestamp",
            ts_mode="epoch-s",
            transcript_glob="{session_id}.jsonl",
            sendable_mode="never",
        ),
    ),
    VendorRow(
        id=AGENT_QWEN,
        install=InstallSpec(command="npm i -g @qwen-code/qwen-code", package="@qwen-code/qwen-code", homepage="https://github.com/QwenLM/qwen-code", binary="qwen", verified=True),
        binary_name="qwen",
        command_env=QWEN_COMMAND_ENV,
        extra_args_env=QWEN_EXTRA_ARGS_ENV,
        # ``qwen [OPTIONS] [query...]``：提示词是**位置参数**（``-p`` 官方标了 deprecated）⇒ 末位放任务；
        # ``-o json`` 出机器读的回包；``--approval-mode yolo`` 放开（``-y`` 同义开关）。
        skip_flag=("--approval-mode", "yolo"),
        skip_permissions_env=QWEN_YOLO_ENV,
        artifact_required=False,
        argv_template=("-o", "json", "{flags}", "{extra}", "{task}"),
        login_hints=("~/.qwen/oauth_creds.json",),
        acp_registry_id="qwen-code",
        acp_args=("--acp",),
        acp_note="11-14 现核：拒了，原话是「Authentication required: Use Qwen Code CLI to authenticate first.」"
                 "⇒ 它自己说没登录",
        resume_note=NO_RESUME_FLAG,
    ),
    VendorRow(
        id=AGENT_COPILOT,
        install=InstallSpec(command="npm i -g @github/copilot", package="@github/copilot", homepage="https://github.com/github/copilot-cli", binary="copilot", verified=True),
        binary_name="copilot",
        command_env=COPILOT_COMMAND_ENV,
        extra_args_env=COPILOT_EXTRA_ARGS_ENV,
        # ``copilot -p <text>``：``-p`` 的值必须**紧跟其后** ⇒ ``-p`` 只能排在末位。
        # ``--allow-all-tools`` 是它自己那枚放开开关（``--allow-all``／``--yolo`` 连路径与 URL 一起开，更宽）。
        # ``--silent`` 只输出回答正文——默认文本里混着统计，回包要干净。
        skip_flag=("--allow-all-tools",),
        skip_permissions_env=COPILOT_ALLOW_ALL_ENV,
        # ``copilot login`` 是浏览器 OAuth（``--web-flow``；远程环境才用 ``--device-code``）⇒
        # 它是**这一族里唯一能一键拉起登录**的一家（授权那一下仍然要主人自己做）。
        login_argv=("login", "--web-flow"),
        # 没测过它是否"exit 0 但没落盘"，不套那条判据（与 Qoder 同理）。
        artifact_required=False,
        argv_template=("{flags}", "--silent", "{extra}", "-p", "{task}"),
        portable_fallback="D:/Portables/cli/copilot/copilot.cmd",
        # 11-08：copilot 的登录痕迹**不在文件里**——OAuth token 进**系统凭据库**（Windows 凭据管理器），
        # PAT 走三个环境变量（官方 ``copilot login --help`` 写明这三条途径与优先级）。
        # 它没有「不烧额度又能报登录态」的子命令 ⇒ 登录态只能靠本地证据判：
        # 三条环境变量与凭据库都没有 ⇒ **没登录**（这一格不许假装能用）。
        login_hints=("env:COPILOT_GITHUB_TOKEN", "env:GH_TOKEN", "env:GITHUB_TOKEN", "cred:copilot"),
        acp_registry_id="github-copilot-cli",
        acp_args=("--acp",),
        acp_note="11-14 现核：给了会话（登录着）",
        resume_note=NO_RESUME_FLAG,
        # ── 会话腿（11-15 现读）─────────────────────────────────────────
        # ``~/.copilot/session-store.db``：会话层是 ``sessions`` 表
        # （id／summary／cwd／created_at／updated_at）；
        # 正文层是 ``turns`` 表，**角色编在列名里**（``user_message``／
        # ``assistant_response``）⇒ 新形状 ``sqlite-turn-pairs``。
        session=SessionSpec(
            vendor=AGENT_COPILOT,
            shape="sqlite-rows",
            transcript_shape="sqlite-turn-pairs",
            order=8,
            db_rule=PathRule(
                candidates=("home:.copilot|session-store.db",),
                pick="first",
            ),
            db_resolver="copilot_db",
            readable_mode="db-exists",
            missing_reason="没找到 GitHub Copilot 的会话库",
            id_column="id",
            title_source="column",
            title_column="summary",
            title_norm="first-line",
            project_mode="column",
            project_column="cwd",
            updated_mode="column-local",
            updated_column="updated_at",
            table="sessions",
            order_by="updated_at desc",
            transcript_table="turns",
            transcript_id_column="session_id",
            transcript_order_column="turn_index",
            transcript_ts_column="timestamp",
            transcript_ts_mode="local",
            turn_user_column="user_message",
            turn_assistant_column="assistant_response",
            sendable_mode="never",
        ),
    ),
    VendorRow(
        id=AGENT_KILO,
        install=InstallSpec(command="npm i -g @kilocode/cli", package="@kilocode/cli", homepage="https://github.com/Kilo-Org/kilocode", binary="kilo", verified=True),
        binary_name="kilo",
        command_env=KILO_COMMAND_ENV,
        extra_args_env=KILO_EXTRA_ARGS_ENV,
        # ``kilo run [message..]``：提示词是**变长位置参数** ⇒ 选项全排它前面
        # （排在后面会被吞进提示词本身，这一族已经吃过一次亏）。
        # 放开那一枚是 ``--auto``；**不是 ``--yolo``**（7.8.27 的 help 里没有那枚）。
        skip_flag=("--auto",),
        skip_permissions_env=KILO_AUTO_ENV,
        artifact_required=False,
        argv_template=("run", "{flags}", "{extra}", "{task}"),
        portable_fallback="D:/Portables/cli/kilo/kilo.cmd",
        # 11-08 18:0x 实跑打脸过一次：**kilo 的「免费层」也要账号**
        # （``kilo run --auto --model kilo/~anthropic/claude-haiku-latest`` ⇒ ``PAID_MODEL_AUTH_REQUIRED``）
        # ⇒ 它没有「不登录也能用」的档，登录痕迹就落在 ``kilo auth list`` 自己打印的那个文件上。
        login_probe=("auth", "list"),
        login_hints=("~/.local/share/kilo/auth.json",),
        acp_registry_id="kilo",
        acp_args=("acp",),
        acp_note="11-14 现核：给了会话（登录着）",
        resume_note=NO_RESUME_FLAG,
        # ── 会话腿（11-15 现读）─────────────────────────────────────────
        # ``~/.local/share/kilo/kilo.db``：会话层 ``session`` 表；
        # 正文层**两张表**：``message``（只说谁说的）＋ ``part``（正文在块里）。
        session=SessionSpec(
            vendor=AGENT_KILO,
            shape="sqlite-rows",
            transcript_shape="sqlite-parts",
            order=11,
            db_rule=PathRule(
                candidates=("home:.local/share/kilo|kilo.db",),
                pick="first",
            ),
            db_resolver="kilo_db",
            readable_mode="db-exists",
            missing_reason="没找到 Kilo Code 的会话库",
            id_column="id",
            title_source="column",
            title_column="title",
            title_norm="first-line",
            project_mode="column",
            project_column="directory",
            updated_mode="column-epoch-ms",
            updated_column="time_updated",
            table="session",
            order_by="time_updated desc",
            transcript_table="message",
            transcript_id_column="session_id",
            transcript_order_column="time_created",
            transcript_ts_column="time_created",
            transcript_ts_mode="epoch",
            transcript_data_column="data",
            transcript_role_field="role",
            transcript_part_table="part",
            transcript_part_message_column="message_id",
            transcript_part_content_column="data",
            transcript_part_order_column="time_created",
            transcript_message_id_column="id",
            # 块那一列是一段 JSON，正文在 ``text`` 那一格。
            user_text_mode="json-field",
            assistant_text_mode="json-field",
            user_text_field="text",
            sendable_mode="never",
        ),
    ),

    # ── 只知道怎么装、还没接派活腿的那几家 ──────────────────────────────
    #
    # 用户 11-15 13:0x：假设用户装的是一台**全新的电脑**，只装了 Plobi ⇒
    # 屏上要能看见这些家，并给出**它们各自**的下载与登录引导。
    #
    # **它们不进通道表**（``offloadable=False``）：派活腿（怎么起它、权限档、
    # 登录判据）本刀**没有实测过**，写进去就是把猜的当事实 ⇒ 屏上只给安装引导，
    # 装好之后这一格仍然是「＋」，并照实说「还没接派活腿」。

    VendorRow(
        id="amp",
        binary_name="amp",
        command_env="PLOBI_OFFLOAD_AMP_COMMAND",
        extra_args_env="PLOBI_OFFLOAD_AMP_EXTRA_ARGS",
        offloadable=False,
        install=InstallSpec(
            command="npm i -g @sourcegraph/amp",
            package="@sourcegraph/amp",
            homepage="https://ampcode.com/",
            binary="amp",
            verified=True,
            note="ACP 官方注册表收录（id「amp-acp」，那是社区维护的适配器；本体这一家还没接派活腿）",
        ),
    ),
    VendorRow(
        id="iflow",
        binary_name="iflow",
        command_env="PLOBI_OFFLOAD_IFLOW_COMMAND",
        extra_args_env="PLOBI_OFFLOAD_IFLOW_EXTRA_ARGS",
        offloadable=False,
        install=InstallSpec(
            command="npm i -g @iflow-ai/iflow-cli",
            package="@iflow-ai/iflow-cli",
            homepage="https://github.com/iflow-ai/iflow-cli",
            binary="iflow",
            verified=True,
            note="官方 CLI 自带实验性 ACP（iflow --experimental-acp），但不在 ACP 官方注册表里；这一家还没接派活腿",
        ),
    ),
    VendorRow(
        id="aider",
        binary_name="aider",
        command_env="PLOBI_OFFLOAD_AIDER_COMMAND",
        extra_args_env="PLOBI_OFFLOAD_AIDER_EXTRA_ARGS",
        offloadable=False,
        install=InstallSpec(
            command="python -m pip install aider-install && aider-install",
            package="aider-install",
            homepage="https://aider.chat",
            binary="aider",
            verified=True,
            note="PyPI 现核（aider-install 0.2.0／本体 aider-chat 0.86.2）；它没有 ACP ⇒ 这一家还没接派活腿",
        ),
    ),
    VendorRow(
        id="goose",
        binary_name="goose",
        command_env="PLOBI_OFFLOAD_GOOSE_COMMAND",
        extra_args_env="PLOBI_OFFLOAD_GOOSE_EXTRA_ARGS",
        offloadable=False,
        install=InstallSpec(
            command="",
            package="",
            homepage="https://block.github.io/goose",
            binary="goose",
            verified=True,
            note="ACP 官方注册表收录（id「goose」）；官方分发是二进制压缩包，按官网装。这一家还没接派活腿",
        ),
    ),
    VendorRow(
        id="crush",
        binary_name="crush",
        command_env="PLOBI_OFFLOAD_CRUSH_COMMAND",
        extra_args_env="PLOBI_OFFLOAD_CRUSH_EXTRA_ARGS",
        offloadable=False,
        install=InstallSpec(
            command="",
            package="",
            homepage="https://charm.land/crush/",
            binary="crush",
            verified=True,
            note="它不在 ACP 官方注册表里 ⇒ 这一家还没接派活腿；官方安装方式见官网（PyPI 上那个同名包是另一个库，不是它）",
        ),
    ),
    VendorRow(
        id="droid",
        binary_name="droid",
        command_env="PLOBI_OFFLOAD_DROID_COMMAND",
        extra_args_env="PLOBI_OFFLOAD_DROID_EXTRA_ARGS",
        offloadable=False,
        install=InstallSpec(
            command="npm i -g droid@1.12.1",
            package="droid",
            homepage="https://app.factory.ai/cli",
            binary="droid",
            verified=True,
            note="ACP 官方注册表收录（id「factory-droid」）；这一家还没接派活腿",
        ),
    ),

    VendorRow(
        id="agoragentic",
        binary_name="agoragentic-mcp",
        command_env="PLOBI_OFFLOAD_AGORAGENTIC_COMMAND",
        extra_args_env="PLOBI_OFFLOAD_AGORAGENTIC_EXTRA_ARGS",
        offloadable=False,
        install=InstallSpec(
            command="npm i -g agoragentic-mcp@1.3.0",
            package="agoragentic-mcp",
            homepage="https://agoragentic.com",
            binary="agoragentic-mcp",
            verified=True,
            note="ACP 官方注册表收录（id「agoragentic-acp」）；安装命令照官方分发写的（registry 与 npm 现核），没在本机实测过。这一家还没接派活腿",
        ),
    ),

    VendorRow(
        id="auggie",
        binary_name="auggie",
        command_env="PLOBI_OFFLOAD_AUGGIE_COMMAND",
        extra_args_env="PLOBI_OFFLOAD_AUGGIE_EXTRA_ARGS",
        offloadable=True,
        dispatch_leg="acp",
        acp_registry_id="auggie",
        acp_args=("--acp",),
        acp_note="11-15 本机实测：握手通了，但登录/派活被拒——它自己说「要在终端里 auggie login，ACP 里登不了」；11-16 它的派活腿接上 ACP（这一族没有一条跑通的命令行腿）",
        resume_note=NO_RESUME_FLAG,
        install=InstallSpec(
            command="npm i -g @augmentcode/auggie@0.37.0",
            package="@augmentcode/auggie",
            homepage="https://www.augmentcode.com/",
            binary="auggie",
            verified=True,
            note="ACP 官方注册表收录（id「auggie」）；11-15 本机实测：握手通了，但登录/派活被拒——"
                 "它自己说「要在终端里 auggie login，ACP 里登不了」⇒ 派活腿走 ACP（11-16 接上）",
        ),
    ),

    VendorRow(
        id="autohand",
        binary_name="autohand",
        command_env="PLOBI_OFFLOAD_AUTOHAND_COMMAND",
        extra_args_env="PLOBI_OFFLOAD_AUTOHAND_EXTRA_ARGS",
        offloadable=False,
        install=InstallSpec(
            command="",
            package="",
            homepage="https://github.com/autohandai/code-cli/blob/main/docs/guides/ACP.md",
            binary="autohand",
            verified=True,
            note="ACP 官方注册表收录（id「autohand」）；官方分发是二进制压缩包，按官网装。这一家还没接派活腿",
        ),
    ),

    VendorRow(
        id="bob",
        binary_name="bob",
        command_env="PLOBI_OFFLOAD_BOB_COMMAND",
        extra_args_env="PLOBI_OFFLOAD_BOB_EXTRA_ARGS",
        offloadable=False,
        install=InstallSpec(
            command="npm i -g @ibm/bob@2.1.0",
            package="@ibm/bob",
            homepage="https://bob.ibm.com",
            binary="bob",
            verified=True,
            note="ACP 官方注册表收录（id「bob」）；安装命令照官方分发写的（registry 与 npm 现核），没在本机实测过。这一家还没接派活腿",
        ),
    ),

    VendorRow(
        id="cortex-code",
        binary_name="cortex",
        command_env="PLOBI_OFFLOAD_CORTEX_CODE_COMMAND",
        extra_args_env="PLOBI_OFFLOAD_CORTEX_CODE_EXTRA_ARGS",
        offloadable=False,
        install=InstallSpec(
            command="",
            package="",
            homepage="https://docs.snowflake.com/en/user-guide/cortex-code/cortex-code",
            binary="cortex",
            verified=True,
            note="ACP 官方注册表收录（id「cortex-code」）；官方分发是二进制压缩包，按官网装。这一家还没接派活腿",
        ),
    ),

    VendorRow(
        id="corust",
        binary_name="corust-agent-acp",
        command_env="PLOBI_OFFLOAD_CORUST_COMMAND",
        extra_args_env="PLOBI_OFFLOAD_CORUST_EXTRA_ARGS",
        offloadable=False,
        install=InstallSpec(
            command="",
            package="",
            homepage="https://github.com/Corust-ai/corust-agent-release",
            binary="corust-agent-acp",
            verified=True,
            note="ACP 官方注册表收录（id「corust-agent」）；官方分发是二进制压缩包，按官网装。这一家还没接派活腿",
        ),
    ),

    VendorRow(
        id="crow-cli",
        binary_name="crow-cli",
        command_env="PLOBI_OFFLOAD_CROW_CLI_COMMAND",
        extra_args_env="PLOBI_OFFLOAD_CROW_CLI_EXTRA_ARGS",
        offloadable=False,
        install=InstallSpec(
            command="",
            package="",
            homepage="https://crow-ai.dev",
            binary="crow-cli",
            verified=True,
            note="ACP 官方注册表收录（id「crow-cli」）；官方分发是二进制压缩包，按官网装。这一家还没接派活腿",
        ),
    ),

    VendorRow(
        id="deepagents",
        binary_name="deepagents-acp",
        command_env="PLOBI_OFFLOAD_DEEPAGENTS_COMMAND",
        extra_args_env="PLOBI_OFFLOAD_DEEPAGENTS_EXTRA_ARGS",
        offloadable=False,
        install=InstallSpec(
            command="npm i -g deepagents-acp@0.1.7",
            package="deepagents-acp",
            homepage="https://docs.langchain.com/oss/javascript/deepagents/overview",
            binary="deepagents-acp",
            verified=True,
            note="ACP 官方注册表收录（id「deepagents」）；安装命令照官方分发写的（registry 与 npm 现核），没在本机实测过。这一家还没接派活腿",
        ),
    ),

    VendorRow(
        id="devin",
        binary_name="devin",
        command_env="PLOBI_OFFLOAD_DEVIN_COMMAND",
        extra_args_env="PLOBI_OFFLOAD_DEVIN_EXTRA_ARGS",
        offloadable=False,
        install=InstallSpec(
            command="irm https://static.devin.ai/cli/setup.ps1 | iex",
            package="",
            homepage="https://docs.devin.ai/cli",
            binary="devin",
            verified=True,
            note="ACP 官方注册表收录（id「devin」）；官方分发是二进制压缩包；"
                 "安装命令照官方 Windows 文档写的（PowerShell 专用），本机实测用的是下载安装器那条路 "
                 "⇒ 这条命令没在本机跑过；这一家还没接派活腿",
        ),
    ),

    VendorRow(
        id="dimcode",
        binary_name="dimcode",
        command_env="PLOBI_OFFLOAD_DIMCODE_COMMAND",
        extra_args_env="PLOBI_OFFLOAD_DIMCODE_EXTRA_ARGS",
        offloadable=False,
        install=InstallSpec(
            command="npm i -g dimcode@0.5.28",
            package="dimcode",
            homepage="https://dimagent.cn/",
            binary="dimcode",
            verified=True,
            note="ACP 官方注册表收录（id「dimcode」）；安装命令照官方分发写的（registry 与 npm 现核），没在本机实测过。这一家还没接派活腿",
        ),
    ),

    VendorRow(
        id="dirac",
        binary_name="dirac",
        command_env="PLOBI_OFFLOAD_DIRAC_COMMAND",
        extra_args_env="PLOBI_OFFLOAD_DIRAC_EXTRA_ARGS",
        offloadable=False,
        install=InstallSpec(
            command="npm i -g dirac-cli@0.6.0",
            package="dirac-cli",
            homepage="https://dirac.run",
            binary="dirac",
            verified=True,
            note="ACP 官方注册表收录（id「dirac」）；安装命令照官方分发写的（registry 与 npm 现核），没在本机实测过。这一家还没接派活腿",
        ),
    ),

    VendorRow(
        id="fast-agent",
        binary_name="fast-agent-acp",
        command_env="PLOBI_OFFLOAD_FAST_AGENT_COMMAND",
        extra_args_env="PLOBI_OFFLOAD_FAST_AGENT_EXTRA_ARGS",
        offloadable=False,
        install=InstallSpec(
            command="uvx fast-agent-acp==0.10.1 -x",
            package="fast-agent-acp==0.10.1",
            homepage="https://fast-agent.ai",
            binary="fast-agent-acp",
            verified=True,
            note="ACP 官方注册表收录（id「fast-agent」）；安装命令照官方分发写的（registry 现核），没在本机实测过。这一家还没接派活腿",
        ),
    ),

    VendorRow(
        id="glm",
        binary_name="glm-acp-agent",
        command_env="PLOBI_OFFLOAD_GLM_COMMAND",
        extra_args_env="PLOBI_OFFLOAD_GLM_EXTRA_ARGS",
        offloadable=True,
        dispatch_leg="acp",
        acp_registry_id="glm-acp-agent",
        acp_args=(),
        acp_note="11-15 本机实测：握手通了，也开出了会话，权限档是 ACP 的 session mode（default／accept_edits／bypass_permissions）；但发一句 prompt 报「No API key found」；11-16 它的派活腿接上 ACP（这一族没有一条跑通的命令行腿）",
        acp_modes=AcpModeMap(
            kind="permission",
            mapping=(("read_only", "default"), ("edit", "accept_edits"),
                     ("full_auto", "bypass_permissions")),
            default="bypass_permissions",
            note="11-15 现读：它的三档就是权限档（default＝问一句；accept_edits＝自动改文件；bypass_permissions＝全放开）",
        ),
        resume_note=NO_RESUME_FLAG,
        install=InstallSpec(
            command="npm i -g glm-acp-agent@1.17.1",
            package="glm-acp-agent",
            homepage="https://github.com/stefandevo/glm-acp-agent",
            binary="glm-acp-agent",
            verified=True,
            note="ACP 官方注册表收录（id「glm-acp-agent」）；11-15 本机实测：握手通了，也开出了会话，"
                 "权限档是 ACP 的 session mode（default／accept_edits／bypass_permissions）；"
                 "但发一句 prompt 报「No API key found」⇒ 派活腿走 ACP（11-16 接上）",
        ),
    ),

    VendorRow(
        id="grok",
        binary_name="grok",
        command_env="PLOBI_OFFLOAD_GROK_COMMAND",
        extra_args_env="PLOBI_OFFLOAD_GROK_EXTRA_ARGS",
        offloadable=True,
        dispatch_leg="acp",
        acp_registry_id="grok-build",
        acp_args=("agent", "stdio"),
        acp_note="11-15 本机实测：握手通了，但开会话被拒——它自己说没登录（authMethods 里是 grok.com 网页登录）；11-16 它的派活腿接上 ACP（这一族没有一条跑通的命令行腿）",
        resume_note=NO_RESUME_FLAG,
        install=InstallSpec(
            command="npm i -g @xai-official/grok@1.0.87",
            package="@xai-official/grok",
            homepage="",
            binary="grok",
            verified=True,
            note="ACP 官方注册表收录（id「grok-build」）；11-15 本机实测：握手通了，但开会话被拒——"
                 "它自己说没登录（authMethods 里是 grok.com 网页登录）⇒ 派活腿走 ACP（11-16 接上）",
        ),
    ),

    VendorRow(
        id="harn",
        binary_name="harn",
        command_env="PLOBI_OFFLOAD_HARN_COMMAND",
        extra_args_env="PLOBI_OFFLOAD_HARN_EXTRA_ARGS",
        offloadable=False,
        install=InstallSpec(
            command="",
            package="",
            homepage="https://harnlang.com",
            binary="harn",
            verified=True,
            note="ACP 官方注册表收录（id「harn」）；官方分发是二进制压缩包，按官网装。这一家还没接派活腿",
        ),
    ),

    VendorRow(
        id="junie",
        binary_name="junie",
        command_env="PLOBI_OFFLOAD_JUNIE_COMMAND",
        extra_args_env="PLOBI_OFFLOAD_JUNIE_EXTRA_ARGS",
        offloadable=False,
        install=InstallSpec(
            command="npm i -g @jetbrains/junie",
            package="@jetbrains/junie",
            homepage="https://github.com/JetBrains/junie",
            binary="junie",
            verified=True,
            note="ACP 官方注册表收录（id「junie」）；本体在 npm 上有官方 scope 的包"
                 "（registry 现核：「Junie command-line client」）⇒ 给这条命令；这一家还没接派活腿",
        ),
    ),

    VendorRow(
        id="kimchi",
        binary_name="kimchi",
        command_env="PLOBI_OFFLOAD_KIMCHI_COMMAND",
        extra_args_env="PLOBI_OFFLOAD_KIMCHI_EXTRA_ARGS",
        offloadable=False,
        install=InstallSpec(
            command="",
            package="",
            homepage="https://kimchi.dev",
            binary="kimchi",
            verified=True,
            note="ACP 官方注册表收录（id「kimchi」）；官方分发是二进制压缩包，按官网装。这一家还没接派活腿",
        ),
    ),

    VendorRow(
        id="kimi",
        binary_name="kimi",
        command_env="PLOBI_OFFLOAD_KIMI_COMMAND",
        extra_args_env="PLOBI_OFFLOAD_KIMI_EXTRA_ARGS",
        offloadable=False,
        install=InstallSpec(
            command="",
            package="",
            homepage="https://moonshotai.github.io/kimi-cli/",
            binary="kimi",
            verified=True,
            note="ACP 官方注册表收录（id「kimi」）；官方分发是二进制压缩包，按官网装。这一家还没接派活腿",
        ),
    ),

    VendorRow(
        id="kimi-code",
        binary_name="kimi",
        command_env="PLOBI_OFFLOAD_KIMI_CODE_COMMAND",
        extra_args_env="PLOBI_OFFLOAD_KIMI_CODE_EXTRA_ARGS",
        offloadable=True,
        dispatch_leg="acp",
        acp_registry_id="kimi-code",
        acp_args=("acp",),
        acp_note="11-15 本机实测：握手通了，但开会话被拒——它自己说没登录（要在终端里 /login）；11-16 它的派活腿接上 ACP（这一族没有一条跑通的命令行腿）",
        resume_note=NO_RESUME_FLAG,
        install=InstallSpec(
            command="npm i -g @moonshot-ai/kimi-code@2.6.2",
            package="@moonshot-ai/kimi-code",
            homepage="https://moonshotai.github.io/kimi-code/",
            binary="kimi",
            verified=True,
            note="ACP 官方注册表收录（id「kimi-code」）；11-15 本机实测：握手通了，但开会话被拒——"
                 "它自己说没登录（要在终端里 /login）⇒ 派活腿走 ACP（11-16 接上）",
        ),
    ),

    VendorRow(
        id="minion-code",
        binary_name="minion-code",
        command_env="PLOBI_OFFLOAD_MINION_CODE_COMMAND",
        extra_args_env="PLOBI_OFFLOAD_MINION_CODE_EXTRA_ARGS",
        offloadable=False,
        install=InstallSpec(
            command="uvx minion-code@0.1.44 acp",
            package="minion-code@0.1.44",
            homepage="https://github.com/femto/minion-code",
            binary="minion-code",
            verified=True,
            note="ACP 官方注册表收录（id「minion-code」）；安装命令照官方分发写的（registry 现核），没在本机实测过。这一家还没接派活腿",
        ),
    ),

    VendorRow(
        id="vibe",
        binary_name="vibe-acp",
        command_env="PLOBI_OFFLOAD_VIBE_COMMAND",
        extra_args_env="PLOBI_OFFLOAD_VIBE_EXTRA_ARGS",
        offloadable=False,
        install=InstallSpec(
            command="",
            package="",
            homepage="https://github.com/mistralai/mistral-vibe",
            binary="vibe-acp",
            verified=True,
            note="ACP 官方注册表收录（id「mistral-vibe」）；官方分发是二进制压缩包，按官网装。这一家还没接派活腿",
        ),
    ),

    VendorRow(
        id="muse-code",
        binary_name="muse",
        command_env="PLOBI_OFFLOAD_MUSE_CODE_COMMAND",
        extra_args_env="PLOBI_OFFLOAD_MUSE_CODE_EXTRA_ARGS",
        offloadable=False,
        install=InstallSpec(
            command="",
            package="",
            homepage="https://dev.meta.ai/docs/muse-code",
            binary="muse",
            verified=True,
            note="ACP 官方注册表收录（id「muse-code」）；官方分发是二进制压缩包，按官网装。这一家还没接派活腿；官网地址这台机器现核连不上（TLS 被挡）⇒ 按官方注册表登的地址给的",
        ),
    ),

    VendorRow(
        id="nova",
        binary_name="nova",
        command_env="PLOBI_OFFLOAD_NOVA_COMMAND",
        extra_args_env="PLOBI_OFFLOAD_NOVA_EXTRA_ARGS",
        offloadable=True,
        dispatch_leg="acp",
        acp_registry_id="nova",
        acp_args=("acp",),
        acp_note="11-15 本机实测：握手通了，但开会话被拒——它自己说「要点 Nova Setup 配 API key」；11-16 它的派活腿接上 ACP（这一族没有一条跑通的命令行腿）",
        resume_note=NO_RESUME_FLAG,
        install=InstallSpec(
            command="npm i -g @compass-ai/nova@1.1.65",
            package="@compass-ai/nova",
            homepage="https://www.compassap.ai/portfolio/nova.html",
            binary="nova",
            verified=True,
            note="ACP 官方注册表收录（id「nova」）；11-15 本机实测：握手通了，但开会话被拒——"
                 "它自己说「要点 Nova Setup 配 API key」⇒ 派活腿走 ACP（11-16 接上）",
        ),
    ),

    VendorRow(
        id="pi",
        binary_name="pi-acp",
        command_env="PLOBI_OFFLOAD_PI_COMMAND",
        extra_args_env="PLOBI_OFFLOAD_PI_EXTRA_ARGS",
        offloadable=False,
        install=InstallSpec(
            command="npm i -g pi-acp@0.0.34",
            package="pi-acp",
            homepage="https://github.com/svkozak/pi-acp",
            binary="pi-acp",
            verified=True,
            note="ACP 官方注册表收录（id「pi-acp」）；安装命令照官方分发写的（registry 与 npm 现核），没在本机实测过。这一家还没接派活腿",
        ),
    ),

    VendorRow(
        id="poolside",
        binary_name="pool-windows-amd64",
        command_env="PLOBI_OFFLOAD_POOLSIDE_COMMAND",
        extra_args_env="PLOBI_OFFLOAD_POOLSIDE_EXTRA_ARGS",
        offloadable=False,
        install=InstallSpec(
            command="",
            package="",
            homepage="https://poolside.ai",
            binary="pool-windows-amd64",
            verified=True,
            note="ACP 官方注册表收录（id「poolside」）；官方分发是二进制压缩包，按官网装。这一家还没接派活腿",
        ),
    ),

    VendorRow(
        id="sigit",
        binary_name="sigit",
        command_env="PLOBI_OFFLOAD_SIGIT_COMMAND",
        extra_args_env="PLOBI_OFFLOAD_SIGIT_EXTRA_ARGS",
        offloadable=False,
        install=InstallSpec(
            command="npm i -g @smbcloud/sigit@1.7.10",
            package="@smbcloud/sigit",
            homepage="https://github.com/getsigit/sigit",
            binary="sigit",
            verified=True,
            note="ACP 官方注册表收录（id「sigit」）；安装命令照官方分发写的（registry 与 npm 现核），没在本机实测过。这一家还没接派活腿",
        ),
    ),

    VendorRow(
        id="stakpak",
        binary_name="stakpak",
        command_env="PLOBI_OFFLOAD_STAKPAK_COMMAND",
        extra_args_env="PLOBI_OFFLOAD_STAKPAK_EXTRA_ARGS",
        offloadable=False,
        install=InstallSpec(
            command="",
            package="",
            homepage="https://stakpak.dev",
            binary="stakpak",
            verified=True,
            note="ACP 官方注册表收录（id「stakpak」）；官方分发是二进制压缩包，按官网装。这一家还没接派活腿",
        ),
    ),

    VendorRow(
        id="vtcode",
        binary_name="vtcode",
        command_env="PLOBI_OFFLOAD_VTCODE_COMMAND",
        extra_args_env="PLOBI_OFFLOAD_VTCODE_EXTRA_ARGS",
        offloadable=False,
        install=InstallSpec(
            command="",
            package="",
            homepage="https://github.com/vinhnx/VTCode/blob/main/docs/guides/zed-acp.md",
            binary="vtcode",
            verified=True,
            note="ACP 官方注册表收录（id「vtcode」）；官方分发是二进制压缩包，按官网装。这一家还没接派活腿",
        ),
    ),

    VendorRow(
        id="deveco",
        binary_name="deveco",
        command_env="PLOBI_OFFLOAD_DEVECO_COMMAND",
        extra_args_env="PLOBI_OFFLOAD_DEVECO_EXTRA_ARGS",
        offloadable=True,
        dispatch_leg="acp",
        acp_args=("acp",),
        acp_note="11-15 本机实测：握手通了、开出了会话，档是 ACP 的 session mode，但它那四档（build／ask／goal／plan）是 agent 模式不是权限档；发一句 prompt 它回 refusal；11-16 它的派活腿接上 ACP（这一族没有一条跑通的命令行腿）",
        acp_modes=AcpModeMap(
            kind="agent",
            note="11-15 现读：它的四档（build／ask／goal／plan）是 agent 模式（干活的那个／问答的那个／出主意的那个），不是权限档 ⇒ 我们不替它选那一档",
        ),
        resume_note=NO_RESUME_FLAG,
        install=InstallSpec(
            command="npm i -g @deveco/deveco-code@stable",
            package="@deveco/deveco-code",
            homepage="https://developer.huawei.com/consumer/cn/doc/harmonyos-guides/ide-deveco-code-install",
            binary="deveco",
            verified=True,
            note="华为的 HarmonyOS 开发 AI Agent（11-15 官方文档现读 ＋ npm registry 现核：stable 0.3.0-stable）；"
                 "它自己实现了 ACP（deveco acp）但不在 ACP 官方注册表里 ⇒ 派活腿走 ACP（11-16 接上）",
        ),
    ),

)


# ── 四张出口表：全部由真源表派生 ───────────────────────────────────────────
#
# 这一段是这一刀的要害：**加一家 ⇒ 下面四张表自己长出来**。
# 每一张的派生函数都收一份 rows 参数 —— 性质测试就是拿「加了一行的表」去跑它们
# （``tests/plobi/test_vendor_registry.py``）。

Rows = tuple[VendorRow, ...]


def external_agent_specs(rows: Rows = VENDOR_ROWS) -> dict[str, ExternalAgentSpec]:
    """**出口表一**：通道表（也是屏上名册的源头）。"""
    return {row.id: row.to_spec() for row in rows if row.offloadable}


def contract_map(rows: Rows = VENDOR_ROWS) -> dict[str, VendorContract]:
    """**出口表二**：契约表（ACP 起法／回跳旗标／会话来源）。"""
    return {row.id: row.to_contract() for row in rows if row.offloadable}


def login_hints_map(rows: Rows = VENDOR_ROWS) -> dict[str, tuple[str, ...]]:
    """**出口表三**：登录痕迹表。

    **没登记的那家不在表里**（不是空元组）—— 调用方据此判「探不到」（``None``），
    不判「没登录」。这一条是这一族的老规矩，不许改。
    """
    return {row.id: row.login_hints for row in rows if row.offloadable and row.login_hints}


def resume_argv_map(rows: Rows = VENDOR_ROWS) -> dict[str, tuple[str, ...]]:
    """**出口表四**：回跳旗标表——**只有实测过的那几家**才有。

    没实测过的旗标不许照着别家的形状编一个——那会让主人点一下跳到别的地方去。
    """
    return {row.id: row.resume_argv for row in rows if row.offloadable and row.resume_argv}


def session_specs(rows: Rows = VENDOR_ROWS) -> dict[str, SessionSpec]:
    """会话读取那一张表（读法由它驱动，见 :class:`SessionSpec`）。

    次序照屏上现在的样子（Qoder、CodeBuddy、Marvis、Cursor、agy、OpenCode）。
    """
    specs = [row.session for row in rows if row.session]
    specs.sort(key=lambda spec: (spec.order, spec.vendor))
    return {spec.vendor: spec for spec in specs}


def login_probe_map(rows: Rows = VENDOR_ROWS) -> dict[str, tuple[str, ...]]:
    """各家自己那条「我登没登」的探针（空 = 没登记 ⇒ 探不到）。"""
    return {row.id: row.login_probe for row in rows if row.offloadable and row.login_probe}


def portable_fallbacks(rows: Rows = VENDOR_ROWS) -> dict[str, str]:
    """便携目录里的落点（这台机器上它不在 PATH 时用；别处没有就当没装）。"""
    return {row.binary_name: row.portable_fallback
            for row in rows if row.offloadable and row.portable_fallback}



def install_specs(rows: Rows = VENDOR_ROWS) -> dict[str, InstallSpec]:
    """**出口表五**：安装事实（一家怎么装）。

    用户 11-15 13:0x：「假设用户的是全新的电脑。只下了我们的 Plobi。用户第一次点配置的『+』的时候。
    没有下载就引导用户下载相关软件并引导登录。」⇒ 这一张表回答「怎么装它」；
    通道表回答「装好之后怎么把活交给它」。**两件事分开记**：
    派活腿没实测过的那些家，这一格照样有，但它们**不进通道表**。
    """
    return {row.id: row.install
            for row in rows if row.install.command or row.install.homepage}


def roster(rows: Rows = VENDOR_ROWS) -> tuple[str, ...]:
    """**屏上名册**：能派活的在前，其余（有官方下载页的候选）在后。

    屏上要能看见「还没接派活腿的那几家」——新用户第一次点「+」时，
    得能看见它们并拿到它们各自的下载引导。
    """
    ready = tuple(row.id for row in rows if row.offloadable)
    rest = tuple(row.id for row in rows
                 if not row.offloadable and (row.install.command or row.install.homepage))
    return ready + rest


def account_rules(rows: Rows = VENDOR_ROWS) -> dict[str, AccountRule]:
    """**出口表六**：多账号——每一族的账号清单从哪读。

    规矩在真源表那一行里（:class:`AccountRule`）；**账号本身在盘上**，
    由 ``plobi/delegation/external_accounts.py`` 按这里的规矩读出来。
    """
    return {row.id: row.accounts_rule for row in rows if row.accounts_rule}


def dispatch_legs(rows: Rows = VENDOR_ROWS) -> dict[str, str]:
    """出口表七：这一家派活**走哪条腿**（``cli``／``acp``）。

    纯 ACP 客席（ACP 官方注册表收录、本机没有一条跑通的命令行腿的那一批）
    登记成 ``acp`` ⇒ 派活时走协议，不走命令行。
    """
    return {row.id: row.dispatch_leg
            for row in rows if row.offloadable and row.dispatch_leg != "cli"}


def acp_mode_maps(rows: Rows = VENDOR_ROWS) -> dict[str, AcpModeMap]:
    """出口表八：ACP 那一腿的**档**怎么对。

    表里声明，代码不按厂商分叉（见 :class:`AcpModeMap`）。
    """
    return {row.id: row.acp_modes for row in rows if row.offloadable and row.acp_modes}


def vendor_row(vendor: str) -> Optional[VendorRow]:
    """认不出的一家 ⇒ ``None``，由调用方决定怎么报——不猜一个默认的。"""
    key = (vendor or "").strip().lower()
    for row in VENDOR_ROWS:
        if row.id == key:
            return row
    return None


EXTERNAL_AGENTS: dict[str, ExternalAgentSpec] = external_agent_specs()
OFFLOAD_CONTRACT: dict[str, VendorContract] = contract_map()
LOGIN_HINTS: dict[str, tuple[str, ...]] = login_hints_map()
RESUME_ARGV: dict[str, tuple[str, ...]] = resume_argv_map()
SESSION_SPECS: dict[str, SessionSpec] = session_specs()
LOGIN_PROBE_ARGV: dict[str, tuple[str, ...]] = login_probe_map()
PORTABLES_FALLBACK: dict[str, str] = portable_fallbacks()
INSTALL_SPECS: dict[str, InstallSpec] = install_specs()
ACCOUNT_RULES: dict[str, AccountRule] = account_rules()
DISPATCH_LEGS: dict[str, str] = dispatch_legs()
ACP_MODE_MAPS: dict[str, AcpModeMap] = acp_mode_maps()


# ── 落点解析（表里的候选怎么变成一条真路径）────────────────────────────────

_PLATFORM_TAGS = ("darwin", "linux", "win32", "other")


def _platform_ok(tag: str) -> bool:
    if not tag or tag not in _PLATFORM_TAGS:
        return True
    if tag == "other":
        return sys.platform != "darwin"
    return sys.platform == tag


def _expand_candidate(candidate: str) -> tuple[Optional[Path], bool]:
    """一条候选 ⇒ （路径，是不是环境变量指过来的）。

    路径为 ``None`` = 这一条在这台机器上不存在（环境变量没设之类）⇒ 跳过。
    """
    text = str(candidate or "").strip()
    if not text:
        return None, False
    tag = ""
    if ":" in text:
        head, _, rest = text.partition(":")
        if head in _PLATFORM_TAGS:
            tag, text = head, rest
    if not _platform_ok(tag):
        return None, False
    kind, _, value = text.partition(":")
    value = value.strip()
    base, _, rel = value.partition("|")
    rel = rel.strip()
    if kind == "env":
        raw = os.environ.get(base.strip(), "").strip()
        if not raw:
            return None, True
        path = Path(os.path.expanduser(raw))
    elif kind == "home":
        path = Path.home() / base.strip()
    elif kind == "xdg":
        raw = os.environ.get(base.strip(), "").strip()
        if not raw:
            return None, False
        path = Path(os.path.expanduser(raw))
    elif kind == "localappdata":
        raw = os.environ.get(base.strip(), "").strip()
        if not raw:
            return None, False
        path = Path(raw)
    elif kind == "appdata":
        raw = os.environ.get(base.strip(), "").strip()
        if not raw:
            return None, False
        path = Path(raw)
    elif kind == "literal":
        path = Path(base.strip())
    else:
        return None, False
    if rel:
        path = path / rel
    return path, kind == "env"



def resolve_candidate(candidate: str) -> Path:
    """把**一条**落点写法（``kind:value|rel``）解成一条真路径。

    :class:`PathRule` 解的是「一串候选里取一个」；账号清单那一族要的是
    **一个文件一个文件地解**（三个身份文件各读各的）⇒ 这里把单条那条拆出来用。
    """
    path, _ = _expand_candidate(candidate)
    return path if path is not None else Path("")


def resolve_path(rule: PathRule) -> Path:
    """按规矩给出一条真路径（表里写候选，这里定取法）。"""
    first: Optional[Path] = None
    for candidate in rule.candidates:
        path, is_env = _expand_candidate(candidate)
        if path is None:
            continue
        if first is None:
            first = path
        if rule.pick == "first":
            return path
        if rule.pick == "env-or-first-existing" and is_env:
            return path
        if path.exists():
            return path
    return first if first is not None else Path("")
