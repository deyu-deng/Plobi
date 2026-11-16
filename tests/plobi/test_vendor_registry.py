"""外包 provider 的**真源表**（WP-OFFLOAD-VENDOR-REGISTRY）：加一家只改一处。

用户 11-15：「**所以你有没有做到一个统一的拓展性强入口。不要硬编码提供商。
然后以后在一个地方添加一个provider多处直接同步。**」

这一档最要紧的是**性质测试**：真源表里加一行 ⇒ 四张出口表**全都长出这一家**。
「加一处、处处同步」从此是**可查的**，不是嘴上说的。

另有两道不许越过的界，各有条钉着：

* **没登记 ⇒ 探不到，不判「没登录」**；
* **没实测过的回跳旗标不许编** —— 表里写的就是「这一家还没有实测过的回跳旗标」。
"""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent import external_offload as channel  # noqa: E402
from agent import vendor_registry as vr  # noqa: E402

#: 四张出口表（加一家 ⇒ 这四张自己长出来）。
OUTLET_TABLES = (
    ("通道表", vr.external_agent_specs, vr.EXTERNAL_AGENTS),
    ("契约表", vr.contract_map, vr.OFFLOAD_CONTRACT),
    ("登录痕迹表", vr.login_hints_map, vr.LOGIN_HINTS),
    ("回跳旗标表", vr.resume_argv_map, vr.RESUME_ARGV),
)


def _new_row(vendor: str = "acme") -> vr.VendorRow:
    """一家**从没见过的** provider（新表里加的那一行长什么样）。"""
    upper = vendor.upper()
    return vr.VendorRow(
        id=vendor,
        binary_name=vendor,
        command_env=f"PLOBI_OFFLOAD_{upper}_COMMAND",
        extra_args_env=f"PLOBI_OFFLOAD_{upper}_EXTRA_ARGS",
        login_hints=(f"~/.{vendor}/.auth",),
        login_probe=("doctor",),
        resume_argv=("--resume", "{session_id}"),
        acp_registry_id=vendor,
        acp_args=("--acp",),
        acp_note="性质测试用的那一行",
    )


# ── 性质：加一行 ⇒ 四张出口表全都长出这一家 ───────────────────────────

def test_adding_one_row_grows_all_four_outlet_tables():
    """**这一刀的要害**：真源表加一行 ⇒ 四张出口表**全都**长出这一家。

    没有这一条，「一处改、处处同步」就只是嘴上说的。
    """
    rows = vr.VENDOR_ROWS + (_new_row(),)

    for name, derive, _current in OUTLET_TABLES:
        grown = derive(rows)
        assert "acme" in grown, f"{name}：加了一行却没长出这一家"


def test_the_outlet_tables_are_exactly_what_the_one_table_derives():
    """出口表**就是**那张表的派生结果（不是各自抄了一份）。

    这一条把上一条接上：真源表里加一行 ⇒ 模块里那四张表**加载后**就会长出这一家。
    """
    for name, derive, current in OUTLET_TABLES:
        assert current == derive(vr.VENDOR_ROWS), f"{name}：与真源表对不上（有人各自抄了一份？）"


def test_adding_one_row_grows_the_session_table_too():
    """会话那一族也是同一张表 ⇒ 加一家（带会话落点）⇒ 会话表也长出这一家。"""
    row = vr.VendorRow(
        id="acme",
        binary_name="acme",
        command_env="",
        extra_args_env="",
        offloadable=False,
        session=vr.SessionSpec(
            vendor="acme",
            shape="jsonl-dir",
            transcript_shape="jsonl-dialogue",
            root_rule=vr.PathRule(candidates=("home:.acme|projects",), pick="first"),
        ),
    )
    rows = vr.VENDOR_ROWS + (row,)

    grown = vr.session_specs(rows)

    assert "acme" in grown
    assert grown["acme"].shape == "jsonl-dir"


# ── 表本身的纪律 ───────────────────────────────────────────────────────

def test_the_roster_is_the_offloadable_rows_and_nothing_else():
    """屏上名册＝通道表＝真源表里能派活的那几家（读会话但没接通道的不在这儿）。"""
    offloadable = [row.id for row in vr.VENDOR_ROWS if row.offloadable]

    assert list(vr.EXTERNAL_AGENTS) == offloadable
    # 12 家走命令行 ＋ 6 家走 ACP ＋ **23 家新接的纯 ACP 客席**（11-16 第十刀第二步：
    # 官方注册表里有 ACP 入口 ⇒ 能派活；装没装只管屏上那个「＋」）。
    assert len(vr.EXTERNAL_AGENTS) == 41


def test_a_vendor_with_no_login_hints_is_not_in_the_login_table():
    """**没登记 ⇒ 探不到，不判「没登录」**：没落点的那家**不在**表里（不是空元组）。"""
    for row in vr.VENDOR_ROWS:
        if not row.login_hints:
            assert row.id not in vr.LOGIN_HINTS, f"{row.id}：没登记落点却进了登录痕迹表"


def test_a_vendor_without_a_measured_resume_flag_says_so_in_words():
    """**没实测过的回跳旗标不许编**：表里写的就是那一句。

    （只查**能派活**的那几家 —— 只读会话、不进通道表的那两家没有回跳旗标这回事。）
    """
    for row in vr.VENDOR_ROWS:
        if not row.offloadable:
            continue
        if row.resume_argv:
            assert row.resume_note == "", f"{row.id}：有旗标却还写着「没实测过」"
            assert row.id in vr.RESUME_ARGV
        else:
            assert row.resume_note == vr.NO_RESUME_FLAG, f"{row.id}：没实测过却没写明"
            assert row.id not in vr.RESUME_ARGV


def test_every_contract_row_matches_its_row_in_the_one_table():
    """契约表与真源表**逐字**对得上（ACP 起法、回跳旗标、会话来源）。"""
    for row in vr.VENDOR_ROWS:
        if not row.offloadable:
            continue
        contract = vr.OFFLOAD_CONTRACT[row.id]
        assert contract.acp_registry_id == row.acp_registry_id
        assert contract.acp_args == row.acp_args
        assert contract.acp_command == row.acp_command
        assert contract.acp_env == row.acp_env
        assert contract.acp_note == row.acp_note
        assert contract.resume_argv == row.resume_argv
        assert contract.resume_note == row.resume_note
        assert contract.session_source == (row.session.vendor if row.session else "")


# ── 公共形状：一个字不许改 ─────────────────────────────────────────────

def test_the_channel_spec_shape_did_not_change():
    """``ExternalAgentSpec`` 的字段与默认值照旧（屏面与工具面照着它读）。"""
    names = [f.name for f in dataclasses.fields(vr.ExternalAgentSpec)]
    assert names == [
        "name", "binary_name", "command_env", "extra_args_env",
        "permission_modes", "default_permission_mode", "permission_mode_env",
        "permission_shape", "permission_prefix", "skip_flag", "skip_permissions_env",
        "login_argv", "scrub_env", "artifact_required", "argv_template",
    ]
    spec = vr.ExternalAgentSpec("x", "x", "A", "B")
    assert spec.permission_shape == "eq"
    assert spec.artifact_required is True
    assert spec.argv_template == ("-p", "{task}", "--output-format", "json", "{flags}", "{extra}")


def test_the_channel_module_still_exports_the_same_names():
    """``from agent import external_offload as channel`` 的调用方零改动。"""
    for name in ("EXTERNAL_AGENTS", "ExternalAgentSpec", "PERMISSION_MODES",
                 "DEFAULT_PERMISSION_MODE", "AGENT_CODEBUDDY", "AGENT_QODER",
                 "AGENT_OPENCODE", "LOGIN_PROBE_ARGV", "PORTABLES_FALLBACK"):
        assert hasattr(channel, name), f"channel.{name} 不见了（调用方会 AttributeError）"
    assert channel.EXTERNAL_AGENTS is vr.EXTERNAL_AGENTS
    assert channel.PERMISSION_MODES == vr.PERMISSION_MODES


def test_the_session_specs_cover_every_vendor_that_has_sessions():
    """九家都在会话表里，次序照屏面。

    前六家是 11-08／11-09 接的；11-15 又接了三家（kiro／Copilot／Qoder 中国版）——
    Qoder 中国版是**另一件安装**（``product: qodercn``、另一个账号、零重叠会话）⇒ 单列一家。
    """
    assert list(vr.SESSION_SPECS) == [
        "qoder", "codebuddy", "marvis", "cursor", "agy", "opencode",
        "kiro", "copilot", "qoder-cn", "gemini", "kilo", "cline",
    ]
    for vendor, spec in vr.SESSION_SPECS.items():
        assert spec.vendor == vendor
        assert spec.shape in ("jsonl-dir", "sqlite-rows", "sqlite-kv-json", "json-session-dir")
        assert spec.transcript_shape in (
            "jsonl-dialogue", "jsonl-steps", "sqlite-dialogue", "sqlite-kv-json",
            "sqlite-turn-pairs", "sqlite-parts", "json-messages")


def test_the_three_new_guests_are_registered_as_data_not_as_code_branches():
    """**加一家只改表**：新三家的判别键、块键、旁挂文件、列名**全在真源表里**。

    读取器里不许出现按厂商分叉的代码 ⇒ 这里把「那份数据长什么样」钉住。
    """
    kiro = vr.SESSION_SPECS["kiro"]
    assert kiro.shape == "jsonl-dir"
    assert kiro.layout == "flat"                       # 会话直接躺在根下，没有项目目录
    assert kiro.sidecar_glob == "{session_id}.json"    # 元数据在旁挂那个 .json 里
    assert kiro.sidecar_title_field == "title"
    assert kiro.sidecar_project_field == "cwd"
    assert kiro.sidecar_updated_field == "updated_at"
    assert kiro.record_kind_field == "kind"           # 判别键不是 type
    assert dict(kiro.kind_roles) == {"Prompt": "user", "AssistantMessage": "assistant"}
    assert kiro.content_block_kind_field == "kind"    # 块的类型写在 kind 里
    assert kiro.content_block_text_field == "data"    # 正文写在 data 里
    assert kiro.ts_path == "data.meta.timestamp"      # 时间在点路径上
    assert kiro.ts_mode == "epoch-s"                  # epoch 秒，不是毫秒
    # 它自己没起出名字时占位写的那两个值 ⇒ 不算标题。
    assert "[user]" in kiro.title_placeholder_values

    copilot = vr.SESSION_SPECS["copilot"]
    assert copilot.shape == "sqlite-rows"
    assert copilot.transcript_shape == "sqlite-turn-pairs"
    assert copilot.turn_user_column == "user_message"
    assert copilot.turn_assistant_column == "assistant_response"

    qoder_cn = vr.SESSION_SPECS["qoder-cn"]
    assert qoder_cn.shape == "jsonl-dir"
    assert qoder_cn.layout == "project-dir"           # 与 qoder 同形


def test_the_vendors_that_were_already_read_keep_the_old_knobs():
    """没登记那些旋钮的家**不受牵连**（老六家一个字都不许变）。

    （新旋钮都有默认值：不登记 sidecar 就不读旁挂文件、不登记 kind_roles 就走老路。）
    """
    for vendor in ("qoder", "codebuddy", "marvis", "cursor", "agy", "opencode"):
        spec = vr.SESSION_SPECS[vendor]
        assert spec.sidecar_glob == ""
        assert spec.title_placeholder_values == ()
        assert spec.record_kind_field == "type"
        assert spec.content_block_kind_field == "type"
        assert spec.content_block_text_field == "text"
        assert spec.ts_path == ""
        assert spec.ts_mode == "raw"
        assert spec.layout == "project-dir"


def test_a_guest_that_cannot_be_sent_to_stays_out_of_the_channel_tables():
    """**能读它的会话 ≠ 能把话续接进它那条会话**——这是两件事。

    kiro／Copilot 的**派活腿**早就接上了（它们在通道表里），但**续接已有会话**的
    回跳旗标没实测过 ⇒ 会话那一格写 ``sendable_mode="never"``。
    Qoder 中国版的派活腿本刀没实测过 ⇒ 整家不进通道表、不进契约表。
    """
    for vendor in ("kiro", "copilot"):
        assert vendor in vr.SESSION_SPECS
        assert vendor in vr.EXTERNAL_AGENTS          # 派活腿接上了
        assert vr.SESSION_SPECS[vendor].sendable_mode == "never"   # 续接已有会话没实测

    assert "qoder-cn" in vr.SESSION_SPECS
    assert "qoder-cn" not in vr.EXTERNAL_AGENTS
    assert "qoder-cn" not in vr.OFFLOAD_CONTRACT


def test_a_session_vendor_that_is_not_offloadable_stays_out_of_the_channel_tables():
    """Marvis／Cursor 有会话可读，但**不是**外包目标 ⇒ 不进通道表、不进契约表。"""
    for vendor in ("marvis", "cursor"):
        assert vendor not in vr.EXTERNAL_AGENTS
        assert vendor not in vr.OFFLOAD_CONTRACT
        assert vendor in vr.SESSION_SPECS


# ── 第五张出口表：安装事实（用户 11-15 13:0x）─────────────────────────

def test_adding_one_row_grows_the_install_table_too():
    """加一行 ⇒ **安装事实那张表**也长出这一家。

    用户 11-15 13:0x：「假设用户的是全新的电脑……用户第一次点配置的『+』的时候。
    没有下载就引导用户下载相关软件并引导登录。」⇒ 「怎么装它」也是同一张表派生出来的。
    """
    rows = vr.VENDOR_ROWS + (
        vr.VendorRow(
            id="acme",
            binary_name="acme",
            command_env="PLOBI_OFFLOAD_ACME_COMMAND",
            extra_args_env="PLOBI_OFFLOAD_ACME_EXTRA_ARGS",
            install=vr.InstallSpec(command="npm i -g acme", homepage="https://example.invalid/acme"),
        ),
    )

    grown = vr.install_specs(rows)

    assert "acme" in grown
    assert grown["acme"].command == "npm i -g acme"


def test_the_install_table_is_exactly_what_the_one_table_derives():
    for name, derive, current in OUTLET_TABLES + (("安装事实表", vr.install_specs, vr.INSTALL_SPECS),):
        assert current == derive(vr.VENDOR_ROWS), f"{name}：与真源表对不上"


def test_every_install_row_has_a_command_or_an_official_page():
    """装不了、也没有官方页的那一家 ⇒ 不进安装事实表（屏上照旧说那句泛泛的话，不编命令）。"""
    for vendor, spec in vr.INSTALL_SPECS.items():
        assert spec.command or spec.homepage, f"{vendor}：既没有命令也没有官方页"


def test_an_unverified_install_fact_says_so_in_words():
    """照官方文档写的（没实测过）必须带一句说明——不许把猜的当事实。"""
    for vendor, spec in vr.INSTALL_SPECS.items():
        if not spec.verified:
            assert spec.note, f"{vendor}：没实测过的安装事实却没有一句说明"


def test_a_row_with_an_acp_entry_is_in_the_channel_tables():
    """**有 ACP 入口 ⇒ 能派活**（11-16 第十刀第二步）。

    11-16 之前有一档叫「只装不派活」，理由是「派活腿没实测过 ⇒ 不许进派活面」。
    机制跑通（11 家真跑）＋ 官方 ACP 注册表给出每家的入口命令之后，**那个理由过期了**
    ⇒ 那一档取消：**装没装只管屏上那个「＋」**，不再决定「能不能派活」。

    屏上的那一格仍由**探针**说话（登录着／它自己说没登录／探不到）⇒ 不会说假话。
    """
    with_entry = []
    for row in vr.VENDOR_ROWS:
        if not (row.acp_args or row.acp_command or "acp" in row.legs):
            continue
        with_entry.append(row.id)
        assert row.offloadable, f"{row.id}：有 ACP 入口却不许派活（那一档已经取消了）"
        assert row.id in vr.EXTERNAL_AGENTS, row.id
        assert row.id in vr.OFFLOAD_CONTRACT, row.id

    assert len(with_entry) >= 40, f"有 ACP 入口的只有 {len(with_entry)} 家（注册表 46 家）"


def test_the_vendors_without_an_acp_entry_stay_out_of_the_channel_tables():
    """**没有 ACP 入口**的那几家仍不进通道表／契约表（照实说，不给点不动的开关）。

    这几家属三种：官方注册表里**没有这一家**（国产/小众 CLI）、
    入口是**另一个可执行文件**（不在 PATH 上，落点不明）、
    入口是 **uvx**（每次现下载 ⇒ Plobi 不代下载）。
    """
    without = [row.id for row in vr.VENDOR_ROWS
               if not (row.acp_args or row.acp_command or "acp" in row.legs)]

    assert without, "表里应当还有几家没接派活腿的"
    for vendor in without:
        assert vendor not in vr.EXTERNAL_AGENTS, vendor
        assert vendor not in vr.OFFLOAD_CONTRACT, vendor

    # 屏上名册看得见它们（新用户第一次点「+」时要能拿到下载引导）——
    # **有官方下载页的那几家**才在屏上；没有下载页的（Marvis 是我们自家、
    # Qoder 中国版是另一件安装）压根不上屏，也就谈不上「看得见」。
    on_screen = [vid for vid in without if vid in vr.INSTALL_SPECS]
    assert on_screen, "表里应当还有几家上了屏却没接派活腿的"
    for vendor in on_screen:
        assert vendor in vr.roster(), vendor


def test_the_channel_table_shape_did_not_change():
    """加了安装事实**不许**改通道表的形状（``ExternalAgentSpec`` 字段与默认值照旧）。"""
    fields = set(vr.ExternalAgentSpec.__dataclass_fields__)

    assert "install" not in fields
    assert vr.EXTERNAL_AGENTS["codebuddy"].binary_name == "codebuddy"


# ── 第六张出口表：多账号（11-16）──────────────────────────────────────

def test_adding_one_row_grows_the_account_table_too():
    """加一行 ⇒ **多账号那张表**也长出这一家（读它的规矩写在那一行里）。

    用户 11-16：「其实我希望可以有多账号体系。」⇒ 多账号**不是一个按厂商分叉的 if**，
    是真源表里的一族事实。
    """
    rows = vr.VENDOR_ROWS + (
        vr.VendorRow(
            id="acme",
            binary_name="acme",
            command_env="",
            extra_args_env="",
            accounts_rule=vr.AccountRule(files=("home:.acme|accounts.json",)),
        ),
    )

    grown = vr.account_rules(rows)

    assert "acme" in grown
    assert grown["acme"].files == ("home:.acme|accounts.json",)


def test_the_account_table_is_exactly_what_the_one_table_derives():
    for name, derive, current in OUTLET_TABLES + (
        ("安装事实表", vr.install_specs, vr.INSTALL_SPECS),
        ("多账号表", vr.account_rules, vr.ACCOUNT_RULES),
    ):
        assert current == derive(vr.VENDOR_ROWS), f"{name}：与真源表对不上"


def test_a_family_without_an_account_rule_has_no_entry_in_the_account_table():
    """没登记规矩的那一族 ⇒ **不进那张表**（不是空规则）。"""
    for row in vr.VENDOR_ROWS:
        if not row.accounts_rule:
            assert row.id not in vr.ACCOUNT_RULES, f"{row.id}：没登记规矩却进了多账号表"


def test_the_codebuddy_family_registers_its_credential_handover():
    """**凭据交接的入口写在表里**（厂商认哪个环境变量）⇒ 读取器里没有按厂商分叉的代码。

    11-16 现读厂商包：非交互的派活腿吃不下它自己那条交互式切换
    （``/login``＝「Switch Tencent Cloud CodeBuddy accounts」）⇒ 走它给非交互进程留的
    那个入口：一个**凭据快照文件**的路径。
    """
    rule = vr.ACCOUNT_RULES["codebuddy"]

    assert rule.credential_env == "CODEBUDDY_AUTH_SNAPSHOT_PATH"
    assert rule.credential_kind == "session"
    assert rule.account_key == "account"
    assert rule.auth_key == "auth"
    # 三个身份文件 = 这一族里三个产品各存各的那一份（次序 = 产品次序）。
    assert len(rule.files) == 3
    assert rule.files[0].endswith("Tencent-Cloud.coding-copilot.info")


def test_the_channel_spec_shape_still_did_not_change():
    """加了多账号那一族 ⇒ **公共形状一个字不许改**（``ExternalAgentSpec`` 照旧）。"""
    names = [f.name for f in dataclasses.fields(vr.ExternalAgentSpec)]

    assert "accounts" not in names
    assert "accounts_rule" not in names


# ── 标题那两个旋钮（11-15 现读修）──────────────────────────────────────

def test_the_vendor_named_title_is_a_knob_not_a_vendor_branch():
    """「厂商自己起的名字」与「剥掉包裹」都写在**表里** ⇒ 不许长成按厂商分叉的代码。

    11-15 真机现读：CodeBuddy／WorkBuddy 的 jsonl 前面有一条 ``ai-title`` 记录
    （``aiTitle``＝它给这条会话起的名字），而第一条 user 消息外面压着
    ``<system-reminder>``、人说的那句裹在 ``<user_query>`` 里。
    ⇒ 两个事实**各写一格**：厂商自起名的那条记录是哪一种、标题取它哪一格；
    没有它时剥掉哪一层包裹。
    """
    spec = vr.SESSION_SPECS["codebuddy"]

    assert spec.title_record_type == "ai-title"
    assert spec.title_record_field == "aiTitle"
    assert spec.title_strip_wrapper == "user_query"


def test_a_vendor_without_those_knobs_keeps_the_old_title_path():
    """没登记那两个旋钮的家 ⇒ 走老路（第一条那个角色说的话），不许被牵连。"""
    spec = vr.SESSION_SPECS["qoder"]

    assert spec.title_record_type == ""
    assert spec.title_record_field == ""
    assert spec.title_strip_wrapper == ""


def test_every_install_only_vendor_opens_a_door_for_a_new_machine():
    """**只装不派活**的每一家都要能拿到「怎么装它」——新用户的第一扇门不许是空的。

    用户 11-15 13:0x：「假设用户的是一台全新的电脑……用户第一次点配置的『+』的时候。
    没有下载就引导用户下载相关软件并引导登录。」

    ⇒ 这一格要么给**命令**、要么给**官方页**；且都要照实说「还没接派活腿」
    （那一格装好之后仍然是「＋」，不给一颗点不动的开关）。
    """
    for row in vr.VENDOR_ROWS:
        if row.offloadable or row.id not in vr.INSTALL_SPECS:
            continue
        spec = vr.INSTALL_SPECS[row.id]
        assert spec.command or spec.homepage, f"{row.id}\uff1a\u65e2\u6ca1\u6709\u547d\u4ee4\u4e5f\u6ca1\u6709\u5b98\u65b9\u9875"
        assert "\u8fd8\u6ca1\u63a5\u6d3e\u6d3b\u817f" in spec.note, f"{row.id}\uff1anote \u91cc\u6ca1\u5199\u660e\u300c\u8fd8\u6ca1\u63a5\u6d3e\u6d3b\u817f\u300d"



# ── ACP 入口那一格（11-16 第十刀）──────────────────────────────────────


def test_the_acp_entry_point_and_the_flags_never_both_get_filled():
    """**两格只许填一格**：``acp_command``（完整 argv）与 ``acp_args``（旗标）。

    都填了就说不清走的是哪条命令 ⇒ 判红。
    """
    for row in vr.VENDOR_ROWS:
        assert not (row.acp_command and row.acp_args), (
            f"{row.id}：acp_command 与 acp_args 只能填一格"
        )


def test_the_contract_row_carries_the_acp_entry_point():
    """契约表那一行**带上**这一格（派活腿与登录探针读的是同一份答案）。"""
    for name, contract in vr.OFFLOAD_CONTRACT.items():
        row = vr.vendor_row(name)
        assert row is not None, name
        assert contract.acp_command == row.acp_command, name
