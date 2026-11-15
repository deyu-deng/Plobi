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
    assert len(vr.EXTERNAL_AGENTS) == 12


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
    """六家（Qoder、CodeBuddy、Marvis、Cursor、agy、OpenCode）都在会话表里，次序照屏面。"""
    assert list(vr.SESSION_SPECS) == ["qoder", "codebuddy", "marvis", "cursor", "agy", "opencode"]
    for vendor, spec in vr.SESSION_SPECS.items():
        assert spec.vendor == vendor
        assert spec.shape in ("jsonl-dir", "sqlite-rows", "sqlite-kv-json")
        assert spec.transcript_shape in (
            "jsonl-dialogue", "jsonl-steps", "sqlite-dialogue", "sqlite-kv-json")


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


def test_the_install_only_vendors_stay_out_of_the_channel_tables():
    """**只装不派活**的那几家不进通道表／契约表（派活腿没实测过 ⇒ 不许进派活面）。"""
    install_only = {vid for vid, spec in vr.INSTALL_SPECS.items()
                    if vid not in vr.EXTERNAL_AGENTS}

    assert install_only, "表里应当有几家只装不派活的候选"

    for vendor in install_only:
        assert vendor not in vr.EXTERNAL_AGENTS
        assert vendor not in vr.OFFLOAD_CONTRACT
        # 但屏上名册看得见它们（新用户第一次点「+」时要能拿到下载引导）。
        assert vendor in vr.roster()


def test_the_channel_table_shape_did_not_change():
    """加了安装事实**不许**改通道表的形状（``ExternalAgentSpec`` 字段与默认值照旧）。"""
    fields = set(vr.ExternalAgentSpec.__dataclass_fields__)

    assert "install" not in fields
    assert vr.EXTERNAL_AGENTS["codebuddy"].binary_name == "codebuddy"


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
