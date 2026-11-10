"""B2: L2 常驻 Agent 注册表。

每项目一个常驻 profile（底座 profiles 机制）；角色→模型路由配置化；
独立会话；L2 直读 Mind 项目子树。

注册表持久化在 ``$PLOBI_HOME/plobi/projects.yaml``（profile-safe：一律走
``get_plobi_home()``，不写死盘符）。格式::

    version: 1
    agents:
      agenda:
        role: l2_agenda
        profile: l2-agenda          # 对应 plobi profile（省略 = 用 agent 名）
        provider: deepseek          # 可选覆盖默认路由；省略 = 用 DEFAULT_ROUTES
        model: deepseek-chat
        mind_subtree: Dossiers/projects/Plobi   # 相对人读面根（L2 直读）
        skills: [morning-report, message-digest]
        description: 日程闭环 Agent

``spawn`` 做三件事：接好共享层那一份（:meth:`AgentRegistry._ensure_shared_layer`，**不再建分身家**，见 WP-REGISTRY-NO-HOME）、
把该 agent 的模型路由写进 profile 的 ``plobi/models.json``（ADR-0011 断言绿）、
输出启动命令。路由断言 ``ModelRouter.assert_valid()`` 保证 L1≠L2 模型。
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

REGISTRY_VERSION = 1

# DEFAULT_ROUTES 里定义的 L1 秘书角色名（spawn 时禁止把 L2 当 L1）。
L1_SECRETARY_ROLE = "l1_secretary"


class RegistryError(ValueError):
    pass


# ---------------------------------------------------------------------------
# 运行时 slug：由名册 name 机械派生（2026-11-05，profile 连根拔 · 后端批）
# ---------------------------------------------------------------------------
#
# 改造前这里有一格 ``AgentEntry.profile``：名册上可以单独填一个 profile 名，
# 于是同一个 Agent 有两处真相——名册 name 与 profile 名。它们一旦分叉，
# ``souls/`` 文件名、``memory/agents/`` 目录、``resolve_agent_slug`` 与 ``-p``
# 参数就会指向不同地方，读侧算不出自己那份身份就静默回落到 L1 总秘书
# （10-31 现核：名册 24 行里 5 行中招）。
#
# 现在 slug 是**派生物**，不是数据：一个 name 只可能派生出同一个 slug。
# 派生不出来（名字形状不合法 / 撞保留名）就回空串——调用方据此不给身份，
# 而不是悄悄落去 L1。

#: 内置映射表（随代码走）。只装「slug 必须与历史一字不差」的那几行，
#: 见 ``runtime_slugs.yaml`` 自己的注释。
RUNTIME_SLUG_TABLE_PATH = Path(__file__).with_name("runtime_slugs.yaml")

#: 与 ``plobi_cli/identity.py::_PROFILE_ID_RE`` **同一条**形状规则。这里内联一份是
#: 有意的：:attr:`AgentEntry.profile_name` 是属性，而那条路径上刻意不 import
#: ``plobi_cli``（见该属性的注释）。两份规则由 :func:`_runtime_slug_shape_rule`
#: 与 tests 钉住。
_RUNTIME_SLUG_RE = re.compile(
    r"^[a-z0-9\u4e00-\u9fff\uff00-\uffef][a-z0-9\u4e00-\u9fff\uff00-\uffef_-]{0,63}$"
)

#: ``plobi_cli.agent_identity._RESERVED_NAMES`` 的同一份清单（理由同上：不能 import）。
#: 撞上它们意味着「这个家永远建不出来」——``plobi`` 就是 Plobi 自己的安装。
_RUNTIME_SLUG_RESERVED = frozenset({"plobi", "default", "test", "tmp", "root", "sudo"})

#: L1（总秘书）在名册里的 name。``default`` 虽是保留名，却是它**专属**的 slug
#: （``runtime_slugs.yaml``: ``secretary: default``）：保留名撞车只对它一人开例外。
_L1_ROSTER_NAME = "secretary"

_runtime_slug_cache: Optional[dict[str, str]] = None
_runtime_slug_cache_stamp: object = object()


def runtime_slug_table_path() -> Path:
    """用户可覆盖的那一份：``$PLOBI_HOME/plobi/runtime-slugs.yaml``（与 projects.yaml 同层）。"""
    from plobi_constants import get_shared_plobi_root

    return get_shared_plobi_root() / "plobi" / "runtime-slugs.yaml"


def _read_runtime_slug_yaml(path: Path) -> dict[str, str]:
    """读一张 ``slugs:`` 表（**保留原始键**）；读不到 / 格式不对就当空表 + warning。"""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        import yaml

        raw = yaml.safe_load(text) or {}
    except Exception as exc:  # noqa: BLE001 - 一张映射表坏掉不该让名册读不出来
        logger.warning("plobi: runtime slug table %s ignored: %s", path, exc)
        return {}
    if not isinstance(raw, dict):
        return {}
    # 两种结构都吃：``slugs:`` 嵌套（老写法）与**扁平顶层映射**（施工单定的形状）。
    nested = raw.get("slugs")
    table = nested if isinstance(nested, dict) else {
        k: v for k, v in raw.items() if not isinstance(v, (dict, list))
    }
    out: dict[str, str] = {}
    for key, value in table.items():
        name = str(key or "").strip()
        slug = str(value or "").strip().lower()
        if name and slug:
            out[name] = slug
    return out


def _merge_slug_table(*tables: "dict[str, str]") -> "dict[str, str]":
    """按 ``casefold`` 去重地合并多张表；**后传的优先**（用户表压内置表）。"""
    merged: "dict[str, str]" = {}
    origin: "dict[str, str]" = {}
    for table in tables:
        for name, slug in table.items():
            key = name.casefold()
            if key in origin:
                merged.pop(origin[key], None)
            origin[key] = name
            merged[name] = slug
    return merged


def runtime_slug_table() -> "dict[str, str]":
    """内置表 + 用户覆盖（同名用户优先），按用户表 mtime 缓存。"""
    global _runtime_slug_cache, _runtime_slug_cache_stamp

    user_path = runtime_slug_table_path()
    # 缓存键必须带上路径本身：开发态与装机版各有各的 PLOBI_HOME（2026-11-05 数据隔离），
    # 只按 mtime 做键的话，两份表"都不存在"（stamp=None）或恰好同一时刻被写，
    # 就会把 A 家的表喂给 B 家的进程——派生出的 slug 会指着别人的 souls/ 与记忆目录。
    try:
        stamp: object = (str(user_path), user_path.stat().st_mtime_ns)
    except OSError:
        stamp = (str(user_path), None)
    if _runtime_slug_cache is not None and _runtime_slug_cache_stamp == stamp:
        return _runtime_slug_cache
    merged = _merge_slug_table(
        _read_runtime_slug_yaml(RUNTIME_SLUG_TABLE_PATH),
        _read_runtime_slug_yaml(user_path),
    )
    _runtime_slug_cache = merged
    _runtime_slug_cache_stamp = stamp
    return merged


def reset_runtime_slug_cache() -> None:
    """丢掉缓存（测试换了 PLOBI_HOME / 改了映射表时用）。"""
    global _runtime_slug_cache, _runtime_slug_cache_stamp

    _runtime_slug_cache = None
    _runtime_slug_cache_stamp = object()


def _slug_from_table(table: "dict[str, str]", name: str) -> str:
    """按「精确 name → name.lower()」两级在表里找 slug；找不到回空串。"""
    if name in table:
        return table[name]
    low = name.lower()
    for key, slug in table.items():
        if key.lower() == low:
            return slug
    return ""


def derive_runtime_slug(name: str) -> str:
    """名册 ``name`` → 运行时 slug（内部标识）；派生不出来回空串。

    规则：
      1. 表里命中（先精确 ``name``，再 ``name.lower()``）→ 用那一条（历史值一字不改）；
      2. 未命中且名字是 ASCII → ``name.lower()``；**非 ASCII 名派生不出 ASCII
         slug，必须显式登记**，未登记回空串；
      3. 结果必须过形状校验（与 ``plobi_cli.agent_identity.validate_profile_name`` 同一条
         规则）且不撞保留名，否则回空串——等于「没有可路由身份」，调用方据此
         不给身份，而不是悄悄落去 L1。

    这是 :attr:`AgentEntry.profile_name` 的唯一实现，也是 ``souls/<slug>.md``、
    ``memory/agents/<slug>/`` 与 ``-p`` 参数的共同来源。
    """
    raw = (name or "").strip()
    if not raw:
        return ""
    slug = _slug_from_table(runtime_slug_table(), raw)
    if not slug:
        if not raw.isascii():
            return ""
        slug = raw.lower()
    if slug.casefold() == "default":
        # ``default`` 是 L1（总秘书）**专属**的 slug（``runtime_slugs.yaml``:
        # ``secretary: default``）。Batch 2a §3：撞保留名一律返空串 = 没有可路由
        # 身份（不抛异常、不 400）。**唯一例外**是 L1 自己那一行——它是 ``default``
        # 这个家的合法主，返空会顺手把 ``plobi -p default`` 弄断。
        return "default" if raw.casefold() == _L1_ROSTER_NAME else ""
    if not _RUNTIME_SLUG_RE.match(slug):
        return ""
    if slug in _RUNTIME_SLUG_RESERVED:
        return ""
    return slug


# R-012: L2 agent taxonomy. ``butler`` is the default so legacy rows
# (no category key) fold into the catch-all — no migration required.
# ``courses`` 是裁定 121 二那一格（用户拍「新建一个 L2 品类 Courses，把课程放进去」）：
# 学在浙大的作业要排进写作业时间， planner 的项目关联靠的是名册里的条目，
# 而这份名册以前只有代码项目 ⇒ 课程以前无处可挂。**品类是代码，条目是数据**：
# 这一格只是让 ``category: courses`` 过校验，往 ``projects.yaml`` 里写课程条目是用户的数据动作。
AGENT_CATEGORIES: tuple[str, ...] = ("projects", "butler", "events", "research", "courses")
DEFAULT_CATEGORY = "butler"

# 无默认目录绑定的两类：``butler`` 是家务——不该被塞进某个内容资产目录（这台
# ``D:\Cloud`` 是百度网盘，工具读不到它，绑过去等于绑进黑洞）。
# 建册时想用别的落点：条目自己的 ``project_path`` 永远优先于这张表（见上面那段注释）。
# **WP-AGENT-CREATE-UPGRADE 改判（用户 10-16）**：``courses`` 移出本表——课程 Agent
# 要能绑文件夹（本学期课程文件夹就在 ``D:\Cloud\Courses`` 下；用户原话「文件夹就在
# Cloud/Courses 下面都有这个学期对应的」）。裁定 121 二当年的「courses 无绑定」是
# 当时没有可绑的东西；现在有了，且用户明确要。但 courses **不进分类默认表**
# （``_WINDOWS_DEFAULT_PROJECT_PATHS`` 的 courses 格保持空）——每门课绑自己的
# 文件夹，没有品类级默认这一说。
UNBOUND_CATEGORIES: tuple[str, ...] = ("butler",)

# R-013 扩写（2026-10-06）：哪几类分身是「Mind 里的一个项目」——只有这两类建册时
# 要在 ``Dossiers/projects/`` 立项（events 挂 agenda 事项、butler 是家务，都不是项目）。
# **裁定 121 二要我明判的那一格：``courses`` 不进这里。** 三条理由：① 课程条目是
# 日程侧的数据，不是要写文档/代码的工作区，挂到脑仓没有消费方；② 脑仓自带 `.git` 与
# 独立远端，`Code` 的闸门碰不到它（裁定 72）——为一个品类去那仓造目录要单独动作，
# 不该由这张表顺手决定；③ 真要立项只是这里加一格， reversible。
MIND_PROJECT_CATEGORIES: frozenset[str] = frozenset({"projects", "research"})

# --------------------------------------------------------------------------- #
# R-013: default folder binding per category. ``butler`` has none (it is the
# housekeeping agent, not project-scoped). A caller may always override
# ``project_path`` on the entry; this map only seeds the default.
#
# The binding is PLATFORM-AWARE. The original table hardcoded a Windows drive
# layout (``D:\...``) for a machine that has since been retired — the repo was
# re-baselined onto this macOS workspace in 2026-09. On macOS/Linux that string
# is just a relative path with a colon in it, so it can never exist;
# 读工作目录那一格（现在叫 :func:`_resolvable_project_cwd`，同一句判据）拒绝编造 cwd，
# 于是每个项目分身悄悄没有工作目录。 Windows keeps the historic bindings
# verbatim; POSIX anchors under the user's home.
#
# Override route (no new PLOBI_* env var — AGENTS.md reserves env/.env for
# secrets): the per-entry ``project_path`` persisted in projects.yaml always
# wins over this table, and ``POST /api/agents`` ``projectPath`` is the
# user-facing way to set it. The table is only the seed for a row that has
# never been bound.
# --------------------------------------------------------------------------- #

_WINDOWS_DEFAULT_PROJECT_PATHS: dict[str, str] = {
    "projects": r"D:\projects",
    "events": r"D:\Cloud\Events",
    "research": r"D:\Cloud\Research",
    "butler": "",
    "courses": "",  # 无绑定，抄 butler 那格先例（见 UNBOUND_CATEGORIES）
}

# Directory under the user's home that anchors projects on macOS/Linux.
POSIX_PROJECTS_ROOT_NAME = "Projects"

# Category → directory name relative to that root. ``projects`` *is* the root;
# ``butler`` has no binding. Table-driven so a new category needs one row here,
# not a condition ladder in the resolver.
_POSIX_CATEGORY_DIRNAME: dict[str, str] = {
    "projects": "",
    "events": "Events",
    "research": "Research",
    "butler": "",
}


def build_default_project_paths(
    *, home: Path | str | None = None, is_windows: bool | None = None
) -> dict[str, str]:
    r"""Category → default folder binding, resolved for one platform.

    Pure and side-effect free: it never touches (or creates) the filesystem,
    because a binding that does not exist must stay a no-op downstream rather
    than a fabricated directory. ``home`` / ``is_windows`` are injectable so a
    test can assert both platforms' invariants without faking ``os.name``.
    """
    if is_windows is None:
        is_windows = os.name == "nt"
    if is_windows:
        return dict(_WINDOWS_DEFAULT_PROJECT_PATHS)
    root = Path(home).expanduser() if home is not None else Path.home()
    projects = root / POSIX_PROJECTS_ROOT_NAME
    resolved: dict[str, str] = {}
    for category in AGENT_CATEGORIES:
        if category in UNBOUND_CATEGORIES:
            resolved[category] = ""
            continue
        subdir = _POSIX_CATEGORY_DIRNAME.get(category, category)
        resolved[category] = str(projects / subdir) if subdir else str(projects)
    return resolved


DEFAULT_PROJECT_PATHS: dict[str, str] = build_default_project_paths()

# 裁定 19: events 生命周期护栏。一个 events 类代理必须挂到一条用户已确认的
# agenda 事项；同时存活 events 类代理上限（超即 400）。
MAX_LIVE_EVENTS_AGENTS = 5


def default_project_path(category: str) -> str:
    """Default folder binding for a category; ``""`` when none (butler)."""
    return DEFAULT_PROJECT_PATHS.get(category, "")


# --------------------------------------------------------------------------- #
# Self-heal: re-anchor rows whose bound folder is not on this machine
#
# Why this exists: 读侧那一格（:func:`_resolvable_project_cwd`）把不存在的
# ``project_path`` 降级成"不给 cwd"（"NEVER fabricate a cwd"）。That is the
# right guard, but it makes stale bindings invisible — the 分身 starts up fine,
# it just never works in its own project directory. Rows went stale wholesale
# when the Windows box was retired and the workspace was re-baselined onto mac:
# every entry still pointed at ``D:\...``, so not one profile got a cwd.
# Re-basing at load time fixes the whole class (Windows-era rows, hand-edited
# yaml, rows imported from another machine) instead of just the seed table.
#
# Contract:
#   * a binding that resolves to a real directory is NEVER touched — a human
#     may have pointed it anywhere they like;
#   * a stale binding is re-resolved as ``<platform default root>/<entry name>``
#     (the entry's own category root first, then the projects root, because the
#     Mind scanner has always filed research projects under the projects root;
#     ``display_name`` is tried after ``name`` for rows renamed by 裁定 37.1);
#   * a re-resolution that does not land on an existing directory is neither
#     written back NOR created — the original value stays so nothing is lost,
#     and the row keeps degrading to a cwd no-op;
#   * idempotent: a healed binding exists on disk, so the next load skips it and
#     writes nothing.
# --------------------------------------------------------------------------- #


def _stale_path_candidate_roots(category: str) -> tuple[str, ...]:
    """Ordered, deduped platform roots a stale binding may be re-based on.

    Empty for a category with no folder binding at all (``butler``) — there is
    nothing to re-base against, so such a row is left alone. ``projects`` is
    always offered after the category's own root because the Mind scanner has
    historically filed research projects there too, not in a research subdir.
    """
    own = default_project_path(category)
    if not own:
        return ()
    roots = [own]
    fallback = default_project_path("projects")
    if fallback and fallback not in roots:
        roots.append(fallback)
    return tuple(roots)


def _is_bindable_name(raw: str) -> bool:
    """Guard the heal against a path-traversal / absolute entry name.

    Entry ids are normally produced by :func:`_project_safe_id`
    (``[A-Za-z0-9_-]+``), but a hand-edited ``projects.yaml`` is untrusted
    input and must not be able to steer the heal outside the default root.
    """
    if not raw or raw in (".", ".."):
        return False
    separators = {os.sep, os.altsep, "/", "\\"} - {None, ""}
    return not any(sep in raw for sep in separators)


def _first_existing_dir(candidates) -> str:
    """First candidate that is an existing directory, else ``""``.

    Read-only by construction — nothing in here creates a directory.
    """
    for candidate in candidates:
        if not candidate:
            continue
        try:
            path = Path(candidate).expanduser()
        except (OSError, ValueError):
            continue
        try:
            if path.is_dir():
                return str(path)
        except OSError:
            continue
    return ""


def resolve_stale_project_path(entry: "AgentEntry") -> str:
    """Re-base a stale ``project_path`` onto this platform's default root(s).

    Returns the corrected path, or ``""`` when no candidate directory actually
    exists — the caller then keeps the original value rather than writing a
    path it made up.
    """
    names = [n for n in (entry.name, entry.display_name) if n and _is_bindable_name(n)]
    return _first_existing_dir(
        str(Path(root).expanduser() / name)
        for root in _stale_path_candidate_roots(entry.category)
        for name in names
    )


def _project_path_is_stale(raw: str) -> bool:
    """True when a binding is set but does not point at a directory here."""
    if not (raw or "").strip():
        return False
    return not _first_existing_dir((raw,))


def _dt_today_iso() -> str:
    """Local date as ISO-8601 (date only) — used for archive folder suffixes."""
    from datetime import date

    return date.today().isoformat()


def _normalize_category(raw: str | None) -> str:
    norm = str(raw or "").strip().lower()
    if norm not in AGENT_CATEGORIES:
        # Fail-closed: an unknown/missing category folds into the catch-all
        # (butler), never an invented grade. No auto-grading.
        return DEFAULT_CATEGORY
    return norm


def default_path() -> Path:
    """注册表文件路径：**共享**的那一份 `plobi/projects.yaml`。

    裁定 42 / WP-PROFILE-SHARED-LAYER（R-096）：profile 活跃时（PLOBI_HOME 指到
    ``<root>/profiles/<name>``）也解析到 root 那一份——名册是共享件，一个脑子一份
    名册；此前每个分身各写各的副本（现核 14 份），正是 R-095 改名改不干净的根因。
    """
    override = os.environ.get("PLOBI_PROJECTS_CONFIG", "").strip()
    if override:
        return Path(override)
    try:
        from plobi_constants import get_shared_plobi_root

        root = get_shared_plobi_root() / "plobi"
    except Exception:
        root = Path.home() / ".plobi" / "plobi"
    return root / "projects.yaml"


def _normalize_project_paths(raw: object, primary: str) -> tuple[str, ...]:
    """多文件夹归一：list/str → 去空去重保序的 tuple；空则回落到主文件夹。

    旧条目只有 ``project_path`` ⇒ 读进来是一元列表。只有清单没有主值时，
    调用方（``from_dict``）把首个元素提为主值，旧消费方看到的主文件夹不变。
    """
    items: list[str] = []
    if isinstance(raw, str):
        raw = [raw]
    if isinstance(raw, (list, tuple)):
        for item in raw:
            text = str(item or "").strip()
            if text and text not in items:
                items.append(text)
    if not items and primary.strip():
        items.append(primary.strip())
    return tuple(items)


@dataclass(frozen=True)
class AgentEntry:
    """注册表里一个常驻 L2 agent 的描述。"""

    name: str
    role: str = "l2_project"
    # 2026-11-05（profile 连根拔 · 后端批）：这里原来是一格可填写的 ``profile`` 名
    # （``profile: str = ""  # 空 = 用 name``）。现在**没有这一格**——运行时 slug
    # 由 :func:`derive_runtime_slug` 按 ``name`` 机械派生（见 :attr:`profile_name`）。
    # 老形状的 ``projects.yaml`` 若还写着 ``profile:``，读盘时静默忽略，下一次
    # ``save()`` 会把那一行键自然抹掉（数据不用手改）。
    provider: str = ""  # 可选覆盖；空 = 默认路由
    model: str = ""  # 可选覆盖；空 = 默认路由
    mind_subtree: str = ""  # 相对 MIND_ROOT 的项目子树（L2 直读）
    skills: tuple[str, ...] = ()
    description: str = ""
    # 这段描述是**自动生成**的（还没被人确认）。描述的落点搬到共享层之后
    # （裁 42／178 乙案）真源就是名册行上这一格——左栏与卡片读的就是它。
    description_auto: bool = False
    # WP-AGENT-DISPLAY-NAME / 裁定 37.1：左栏 / 卡片显示的人名（保留大小写）。
    # ``name`` 永远是小写 slug（id）；``display_name`` 空时回退 id。description
    # 不再承担显示名职责——它继续存长标语 / 一句话意图。
    display_name: str = ""
    # C5 项目节奏：{"weekly_hours": N}。仅对 role=l2_project 有意义；
    # None = 未配置（规划员不为其排推进块）。
    pace: Optional[dict] = None
    # R-012: sidebar taxonomy group. ``butler`` default for backward compat.
    category: str = DEFAULT_CATEGORY
    # R-013: folder binding. Empty = no bound folder (butler, or unset).
    # When the caller does not supply one, the registry seeds it from the
    # category default on create (see ``AgentRegistry.upsert``).
    project_path: str = ""
    # WP-AGENT-CREATE-UPGRADE（用户 10-16 原话「一个 Agent 肯定也要可以添加多个文件夹」）：
    # 多文件夹扩展。``project_path`` 保持**主文件夹**语义（第一个，继续驱动
    # :func:`profile_config_overlay`（读时算 ``terminal.cwd``）与一切旧消费方）；
    # ``project_paths`` 是完整清单。
    # 加载时互相归一：只有主值的旧条目读进来 = 一元列表；只有清单时主值取首个。
    project_paths: tuple[str, ...] = ()
    # WP-AGENT-CREATE-UPGRADE（用户 10-16：「之前底座还可以选择图标和颜色。我觉得很可爱」）：
    # 与项目条目同形（projects 的 icon/color 在 store/projects.py）——建册时可选，
    # 左栏/卡片按它渲染。空 = 用既有默认（profile 确定性色 / 无图标）。
    icon: str = ""
    color: str = ""
    # 裁定 19 dissolve: archived rows stay on disk but hide from the default
    # list. ``archived_at`` is the ISO date the profile dir was moved aside.
    archived: bool = False
    archived_at: str = ""
    # events 生命周期：建册时指向一条用户已确认的 agenda 事项 id。
    # Non-empty only for category=events (validated at the API layer).
    source_event_id: str = ""
    # 裁定 42 §42.2 第 6 项的三个允许覆盖里的第三个（人设片段 = 分身的 SOUL.md
    # 围栏外那段、模型 = ``provider`` / ``model``、密钥 = 这一段）。以前密钥
    # 没有字段，靠 ``create_profile(clone_config=True)`` 整份拷根 ``.env``——
    # 实测 13 个分身的 ``.env`` MD5 全同，等于没有覆盖只有复印件。这里的键值
    # 只在运行时注入子进程环境（:func:`apply_secret_overrides`），不落盘成
    # 第二份 ``.env``。
    secret_overrides: dict = field(default_factory=dict)
    # R-072（2026-10-07 用户实测）：名册成员资格是**盖过章的事实**，不是猜出来的。
    # 只有两个写入方——「新建 / 登记一个 Agent」那一步盖 ``True``（控制台建册、
    # ``plobi plobi agents register``、系统自带的 agenda 模板），Mind 播种机的投影
    # 盖 ``False``（:func:`ensure_mind_project_agents`）。判据**不看 description
    # 里那句话长什么样**：以前 ``is_mind_placeholder`` 靠占位句前缀认「用户配的」，
    # 那句话一抖（改措辞、用户补了说明、播种机多缀一段 raw 名）手下就从名单里消失。
    # 老数据没盖过章时按 ``True`` 读：宁可多列一行让他问一句，也不许把用户建过的
    # 分身悄悄藏掉——藏掉才是这次要修的那个 bug。
    registered: bool = True

    # 全局真源 + per-agent 开关（2026-10-19 用户裁定，收掉 179「按家分持」的
    # 最后一层含义）：技能 / 工具集 / MCP 服务器只有**一张全局表**（顶层
    # config.yaml 的 skills 目录、``agent.disabled_toolsets``、``mcp_servers``），
    # 每个 Agent 默认全量拥有。这里三份**黑名单**只记「这个 Agent 关掉了哪些」，
    # 是偏好不是拷贝——装一个新 skill / MCP 全员立刻可见，关的只是这一个 Agent
    # 的可见性。消费点做并集减法：skill_utils.get_disabled_skill_names（技能）、
    # cli 的 agent 构建（工具集，browser 一族就是 toolset）、
    # tools/mcp_tool.py 服务器装载（MCP）。
    disabled_skills: tuple[str, ...] = ()
    disabled_toolsets: tuple[str, ...] = ()
    disabled_mcp_servers: tuple[str, ...] = ()

    @property
    def profile_name(self) -> str:
        # Profiles are stored lowercase on disk and the CLI's ``-p`` pre-scan
        # drops any value failing ``_PROFILE_ID_RE`` — so handing out the
        # registry key verbatim (``Aura``) makes ``plobi --profile Aura serve``
        # fall through to argparse, which then reads ``Aura`` as a subcommand
        # and exits 2. The base documents the same requirement: normalize before
        # validation / subprocess spawn (plobi_cli/identity.py:303-318, #18498).
        #
        # Normalized inline instead of importing that helper: this module
        # deliberately avoids depending on plobi_cli at attribute time (see the
        # lazy imports below), and even a lazy import breaks under the test
        # harness, where ``plobi_cli`` does not resolve to the real module.
        # tests/plobi_cli/test_agent_identity.py pins the two rules together.
        # 数据角色（``l2_course``）不是分身：它**没有运行时可连**，所以对外也不该有一个
        # 看起来能连的 profile 名。以前这里一律回退到条目名，于是名册里那五门课各自
        # 冒充一格 profile ⇒ 桌面为它们起 `plobi -p <课名>`：ASCII 名退 1
        # （`Profile 'X' does not exist`）、非 ASCII 名被 CLI 的 `-p` 预扫丢掉退 2
        # （就是下面这段注释本来预警的形状），每一门都是一场重启循环
        # （2026-10-13 现核 desktop.log：单「电工电子学」2767 行 failed）。
        # 回空串而不是加一枚 `runtime: false` 标志：标志要每个消费方记得去看，
        # 空串是这里本来就有的形状（``agent_askability`` 1019 行那条判据在判它）。
        #
        # ⚠️ **这段注释的前半句已被后续裁定推翻，别照它给课程行判死刑**（2026-10-20
        # 实录：有人读了这几行就以为「课程是数据、不可点」，把 courses 品类的行批量
        # 打回 l2_course，结果与三条裁定相反）：
        #   * 裁定 143②：课程要有可路由的 L2 身份，课程 Agent 与项目分身**同列同点**；
        #   * 裁定 145①(b)：课程作业/考试的派发以那个身份为前提；
        #   * 裁定 146②(b)：要么给身份让它可点，要么那一栏不显示，**不许列在那儿点不动**；
        #   * 用户原话（10-15）：「课程Agent还是不能点击」「左边的五种Agent要完全平等」。
        # 名册里的课程行走的就是这条路：``role: l2_project`` + ``category: courses``
        # + 显式 ASCII ``profile: l2-course-*``，家裸建（12 KB）。所以
        # ``l2_course`` 只描述「**还没有**身份的课程条目」这一种形状，不是课程行的
        # 正统。课程行的点击资格按 :func:`agent_navigability` 判（那条只否决点了必然
        # 失败的形状：没身份 / 保留名 / 家没落地）。
        from plobi.routing.models import L2_COURSE

        if self.role == L2_COURSE:
            return ""
        # 2026-11-05：**派生物，不是数据**。以前这里是 ``self.profile or self.name``
        # ——名册上多一个可填写的一格，就有了「name 与 profile 名不一致」这条分叉；
        # 现在只读 ``name``，并在 ``runtime_slugs.yaml`` 里为历史那 14 行钉住旧值，
        # 保证 ``souls/<slug>.md`` / 记忆目录 / ``-p`` 参数一个都不变。
        return derive_runtime_slug(self.name)

    @property
    def has_model_override(self) -> bool:
        return bool(self.provider or self.model)

    @property
    def weekly_hours(self) -> Optional[float]:
        """``pace.weekly_hours`` 的规范化读取；未配置或非法时为 None。"""
        if not isinstance(self.pace, dict):
            return None
        try:
            hours = float(self.pace.get("weekly_hours"))
        except (TypeError, ValueError):
            return None
        if hours <= 0 or hours > 168:
            return None
        return hours

    def bound_paths(self) -> tuple[str, ...]:
        """全部绑定文件夹（主文件夹在前）。单值旧条目 = 一元组；无绑定 = 空。"""
        if self.project_paths:
            return tuple(self.project_paths)
        if self.project_path:
            return (self.project_path,)
        return ()

    def to_dict(self) -> dict:
        data: dict = {"role": self.role}
        # 2026-11-05：``profile`` 不再落盘、不再透出——slug 由 name 派生，
        # 写进 YAML 只会让它第二次变成「另一处真相」。
        if self.provider:
            data["provider"] = self.provider
        if self.model:
            data["model"] = self.model
        if self.mind_subtree:
            data["mind_subtree"] = self.mind_subtree
        if self.skills:
            data["skills"] = list(self.skills)
        if self.disabled_skills:
            data["disabled_skills"] = list(self.disabled_skills)
        if self.disabled_toolsets:
            data["disabled_toolsets"] = list(self.disabled_toolsets)
        if self.disabled_mcp_servers:
            data["disabled_mcp_servers"] = list(self.disabled_mcp_servers)
        if self.description:
            data["description"] = self.description
        if self.description_auto:
            data["description_auto"] = True
        if self.display_name:
            data["display_name"] = self.display_name
        if self.pace:
            data["pace"] = self.pace
        # R-012/R-013: always write category so roundtrip is faithful; the
        # default (butler) is explicit so a human reading projects.yaml sees
        # the grade. project_path only when non-empty (butler has none).
        data["category"] = self.category
        if self.project_path:
            data["project_path"] = self.project_path
        # WP-AGENT-CREATE-UPGRADE: 多文件夹清单（一元时不写，旧形状原样保留）。
        if self.project_paths:
            data["project_paths"] = list(self.project_paths)
        # 图标/颜色（用户 10-16 要回的「可爱」）：建册可选，空 = 默认渲染。
        if self.icon:
            data["icon"] = self.icon
        if self.color:
            data["color"] = self.color
        if self.archived:
            data["archived"] = True
            if self.archived_at:
                data["archived_at"] = self.archived_at
        if self.source_event_id:
            data["source_event_id"] = self.source_event_id
        if self.secret_overrides:
            data["secret_overrides"] = dict(self.secret_overrides)
        # R-072: 名册成员资格永远写进 yaml（和 ``category`` 同一口径）——这一格
        # 是「谁把这条登记成手下」的账面证据，读 yaml 的人要能直接看见，不靠猜。
        data["registered"] = bool(self.registered)
        return data

    @classmethod
    def from_dict(cls, name: str, raw: dict) -> "AgentEntry":
        if not isinstance(raw, dict):
            raise RegistryError(f"agent {name!r}: entry must be a mapping")
        skills = raw.get("skills") or []
        if isinstance(skills, str):
            skills = [skills]
        pace = raw.get("pace")
        if pace is not None and not isinstance(pace, dict):
            raise RegistryError(f"agent {name!r}: pace must be a mapping")
        if isinstance(pace, dict) and "weekly_hours" in pace:
            try:
                hours = float(pace["weekly_hours"])
            except (TypeError, ValueError) as exc:
                raise RegistryError(
                    f"agent {name!r}: pace.weekly_hours must be a number"
                ) from exc
            if hours <= 0 or hours > 168:
                raise RegistryError(
                    f"agent {name!r}: pace.weekly_hours must be in (0, 168]"
                )
            pace = {"weekly_hours": hours}
        else:
            pace = None
        secrets = raw.get("secret_overrides")
        if secrets is not None and not isinstance(secrets, dict):
            raise RegistryError(f"agent {name!r}: secret_overrides must be a mapping")
        primary = str(raw.get("project_path") or "").strip()
        paths = _normalize_project_paths(raw.get("project_paths"), primary)
        if not primary and paths:
            primary = paths[0]
        return cls(
            name=name,
            role=str(raw.get("role") or "l2_project"),
            # 老形状的 ``profile:`` 键在这里被**静默忽略**（2026-11-05 降级为派生）。
            provider=str(raw.get("provider") or ""),
            model=str(raw.get("model") or ""),
            mind_subtree=str(raw.get("mind_subtree") or ""),
            skills=tuple(str(s) for s in skills),
            disabled_skills=tuple(
                str(s) for s in (raw.get("disabled_skills") or ()) if str(s).strip()
            ),
            disabled_toolsets=tuple(
                str(s) for s in (raw.get("disabled_toolsets") or ()) if str(s).strip()
            ),
            disabled_mcp_servers=tuple(
                str(s) for s in (raw.get("disabled_mcp_servers") or ()) if str(s).strip()
            ),
            description=str(raw.get("description") or ""),
            description_auto=bool(raw.get("description_auto") or False),
            display_name=str(raw.get("display_name") or ""),
            pace=pace,
            category=_normalize_category(raw.get("category")),
            project_path=primary,
            project_paths=paths,
            icon=str(raw.get("icon") or ""),
            color=str(raw.get("color") or ""),
            archived=bool(raw.get("archived") or False),
            archived_at=str(raw.get("archived_at") or ""),
            source_event_id=str(raw.get("source_event_id") or ""),
            registered=_normalize_registered(name, raw.get("registered")),
            secret_overrides={
                str(key): str(value)
                for key, value in (secrets or {}).items()
                if str(key).strip() and str(value).strip()
            },
        )


#: ``registered`` 里认得下的真话写法（人手写 yaml 时 ``yes`` / ``1`` 都会出现）。
_TRUTHY_REGISTERED = ("true", "yes", "y", "on", "1")
_FALSY_REGISTERED = ("false", "no", "n", "off", "0")


def _normalize_registered(name: str, value: object) -> bool:
    """读 ``registered`` 那一格：只认明确的写法，读不懂就报错，不猜。

    缺格 = :data:`True`（老数据没盖过章 → 按「用户登记过」读，见
    :attr:`AgentEntry.registered`：宁可多列一行，也不悄悄把手下藏掉）。
    一句 ``registered: maybe`` 不能既不当真话也不当假话——那是这次要拆掉的
    那种「靠猜」，所以直接 :class:`RegistryError`。
    """
    if value is None:
        return True
    if isinstance(value, bool):
        return value
    text = str(value).strip().casefold()
    if text in _TRUTHY_REGISTERED:
        return True
    if text in _FALSY_REGISTERED:
        return False
    raise RegistryError(
        f"agent {name!r}: registered must be true or false (got {value!r})"
    )


def _case_twin_message(new_key: str, existing_key: str, home: str) -> str:
    """只差大小写撞了已有键那句话（唯一一份措辞：``upsert`` 兜底与创建门都读它）。"""
    return (
        f"「{new_key}」和已有的「{existing_key}」只差大小写——两个的名字落到盘上是同一个家"
        f"（{home or new_key}），登记两条就谁也问不到。"
        f"要么直接用「{existing_key}」那条，要么换一个真正不同的名字。"
    )


@dataclass
class AgentRegistry:
    """读写 projects.yaml 的注册表。"""

    path: Path
    agents: dict[str, AgentEntry] = field(default_factory=dict)

    # ------------------------------------------------------------------ #
    # IO
    # ------------------------------------------------------------------ #

    @classmethod
    def load(cls, path: Path | str | None = None) -> "AgentRegistry":
        target = Path(path) if path else default_path()
        agents: dict[str, AgentEntry] = {}
        if target.exists():
            try:
                import yaml

                raw = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
                if isinstance(raw, dict):
                    entries = raw.get("agents") or {}
                    if isinstance(entries, dict):
                        for name, entry in entries.items():
                            try:
                                agents[str(name)] = AgentEntry.from_dict(str(name), entry)
                            except RegistryError as exc:
                                logger.warning("plobi registry: %s", exc)
            except Exception:
                logger.warning("plobi: could not read %s; treating as empty", target)
        registry = cls(path=target, agents=agents)
        # Self-heal bindings left over from another machine / an older OS.
        # Best-effort: a read-only home or an unreadable volume must not stop
        # the registry from loading, same as the read above.
        try:
            registry.heal_stale_project_paths()
        except Exception as exc:
            logger.warning("plobi registry: project_path self-heal skipped: %s", exc)
        return registry

    def heal_stale_project_paths(self, *, save: bool = True) -> list[str]:
        """Re-anchor rows whose ``project_path`` is not a directory here.

        See the *Self-heal* block above :func:`resolve_stale_project_path` for
        the full contract (never touch a live binding, never fabricate a path,
        never create a directory, idempotent). Returns the names corrected on
        this call and writes the registry back to ``self.path`` **only** when
        something was actually corrected — a second load rewrites nothing.
        """
        from dataclasses import replace

        healed: list[str] = []
        for name, entry in list(self.agents.items()):
            current = (entry.project_path or "").strip()
            if not _project_path_is_stale(current):
                # Either unbound (butler / never seeded) or a real directory a
                # human chose. Both are left exactly as they are.
                continue
            fixed = resolve_stale_project_path(entry)
            if fixed:
                self.agents[name] = replace(entry, project_path=fixed)
                healed.append(name)
                logger.warning(
                    "plobi registry: %s project_path %r is not a directory on "
                    "this machine; re-based to %r",
                    name,
                    current,
                    fixed,
                )
            else:
                # Fail-closed: keep the original value, create nothing. The row
                # stays unbound in practice because :func:`_resolvable_project_cwd`
                # skips a path that does not exist, so the 分身 gets no cwd instead
                # of a fabricated one.
                logger.warning(
                    "plobi registry: %s project_path %r is not a directory and "
                    "no default root matches it; leaving the binding as-is "
                    "(terminal.cwd stays unset for this profile)",
                    name,
                    current,
                )
        if healed and save:
            try:
                self.save()
            except Exception as exc:
                logger.warning(
                    "plobi registry: project_path self-heal write-back skipped "
                    "for %s: %s",
                    healed,
                    exc,
                )
        return healed

    def save(self, path: Path | str | None = None) -> Path:
        target = Path(path) if path else self.path
        target.parent.mkdir(parents=True, exist_ok=True)
        import yaml

        payload = {
            "version": REGISTRY_VERSION,
            "agents": {name: entry.to_dict() for name, entry in sorted(self.agents.items())},
        }
        text = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True)
        # 原子落盘：先写同目录的临时文件，再 rename 覆盖。旧写法是 `write_text`
        # 直接覆写——名册有 24 行、每次改一格都要整档重写，而左栏那次
        # `GET /api/agents` 是并发读同一个文件：撞上写窗口就读到半截（或空）文件，
        # 于是 2026-10-26 出现「改完一个 Agent 的工作目录，左栏全部消失」。
        # `os.replace` 在同一目录内是原子的（Windows 与 POSIX 都是），读者要么看到
        # 旧的一份完整文件、要么看到新的一份完整文件，不会看到中间态。
        tmp = target.with_name(f"{target.name}.tmp-{os.getpid()}")
        try:
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, target)
        finally:
            tmp.unlink(missing_ok=True)
        return target

    # ------------------------------------------------------------------ #
    # CRUD
    # ------------------------------------------------------------------ #

    def get(self, name: str) -> Optional[AgentEntry]:
        return self.agents.get(name)

    def find_case_twin(self, name: str) -> str:
        """已存在的、与 *name* 只差大小写的注册键（没有则空串）。

        profile 家一律按小写名落盘（:attr:`AgentEntry.profile_name`），所以
        ``Nymo`` 与 ``nymo`` 是**同一个家的两条记录**：问分身时两条一起命中，
        秘书谁都问不到（R-071 实测的「对上了好几个分身（Nymo、Nymo）」）。
        """
        needle = (name or "").strip().casefold()
        if not needle:
            return ""
        for key in self.agents:
            if key != name and key.strip().casefold() == needle:
                return key
        return ""

    def find_mind_subtree_owner(
        self, relative: str, *, exclude: str = ""
    ) -> str:
        """哪条**没归档**的分身已经挂着这棵 Mind 树（大小写归一比，没有则空串）。

        一个项目一棵树：``Dossiers/projects/Nymo`` 挂了 ``Nymo`` 之后再建一条挂同一棵树的分身，
        就是用户 2026-10-06 报的「左栏两个 nymo」。比的是归一后的整串相对路径——不分大小写的
        盘上 ``Dossiers/projects/nymo`` 与 ``Dossiers/projects/Nymo`` 也是同一棵树。
        """
        from plobi.mind.paths import normalize_logical

        needle = normalize_logical(relative or "").strip().casefold()
        if not needle:
            return ""
        for other in self.live_entries():
            if other.name == exclude:
                continue
            theirs = normalize_logical(other.mind_subtree or "").strip().casefold()
            if theirs == needle:
                return other.name
        return ""

    def upsert(self, entry: AgentEntry) -> AgentEntry:
        # 新键不许只差大小写地撞上已有条目——那等于给同一个 profile 家登记两个
        # 分身。改已有条目（键相同）不在此列：存量 ``Nymo``/``nymo`` 已经成对躺在
        # projects.yaml 里，读它们 / 补它们的字段不许报错（R-071 要的是不再新增，
        # 不是替用户改他的数据）。
        # 2b-1 风险②：``default`` 是 L1 总秘书的保留名。名册里若真有一行叫
        # default，桌面端的分桶键（L1 空串 → ``default``）会和它撞在一起，两边
        # 会话被合并。这里必须**明确拒绝**并给出可读错误串，而不是让上层回 400
        # 空响应——用户看得到原因才知道要改名。
        # 存量行不受影响：只在新增（键还不在表里）时拦，不动用户已有数据。
        if entry.name not in self.agents and (entry.name or "").strip().lower() == "default":
            raise RegistryError(
                f"agent name {entry.name!r} is reserved for the L1 secretary "
                f"(slug {_L1_ROSTER_NAME!r} → 'default'); rename the agent and retry"
            )

        if entry.name not in self.agents:
            twin = self.find_case_twin(entry.name)
            if twin:
                raise RegistryError(
                    _case_twin_message(entry.name, twin, entry.profile_name)
                )
        # R-013: when the caller did not supply any folder binding, seed it from
        # the category default. butler stays empty (no bound folder). The
        # actual directory is created at the API layer (console router),
        # not here — registry stays pure data + no filesystem mutation
        # beyond its own yaml.
        # WP-AGENT-CREATE-UPGRADE：判据用 bound_paths()（清单或主值任一在都算
        # 已绑定）——只查 project_path 会把「带了清单没带主值」的条目误判成未绑定，
        # 被品类默认覆盖掉。
        if not entry.bound_paths() and entry.category not in UNBOUND_CATEGORIES:
            from dataclasses import replace

            seeded = default_project_path(entry.category)
            if seeded:
                entry = replace(entry, project_path=seeded, project_paths=(seeded,))
        # 2026-11-05：这里原来有一段「派生名撞保留名 ⇒ 把推荐名落成显式 profile」的
        # 自愈逻辑（项目分身叫 ``Plobi`` 时内部家写成 ``l2-plobi``）。那一格已经没了：
        # slug 由 name 派生，这类撞保留名的历史行改由 ``runtime_slugs.yaml`` 的映射表
        # 钉住（``Plobi: l2-plobi``）。登记路径不再改写用户数据，也不再需要那次
        # ``plobi_cli`` 的探测（原来的 try/ImportError 兜底随之消失）。
        self.agents[entry.name] = entry
        return entry

    def _profile_taken_by_other(self, profile: str, name: str) -> bool:
        """推荐名已经被别的条目占着 ⇒ 不许自动落，留给 ``profile_name_blocker`` 说。"""
        target = (profile or "").strip().lower()

        return any(other.name != name and other.profile_name.strip().lower() == target
                   for other in self.agents.values())

    def remove(self, name: str) -> bool:
        return self.agents.pop(name, None) is not None

    def dissolve(self, name: str, *, archived_at: str = "") -> AgentEntry:
        """裁定 19: 归档不删除。

        把注册表条目标记 ``archived=True``（保留，列表默认不显示）。
        profile 目录的物理搬运由调用方（console router）在标记前后做，
        因为路径解析依赖 ``plobi_cli.agent_identity`` —— registry 保持纯数据。
        返回归档后的 entry；找不到则 ``RegistryError``。
        """
        entry = self.get(name)
        if entry is None:
            raise RegistryError(f"agent {name!r} is not registered")
        if entry.archived:
            return entry  # idempotent: 已归档不再重复
        from dataclasses import replace

        stamp = archived_at or _dt_today_iso()
        updated = replace(
            entry,
            archived=True,
            archived_at=stamp,
        )
        self.agents[name] = updated
        return updated

    def names(self) -> list[str]:
        return sorted(self.agents)

    def entries(self) -> list[AgentEntry]:
        return [self.agents[n] for n in self.names()]

    # ------------------------------------------------------------------ #
    # R-012 / 裁定 19: taxonomy + lifecycle helpers
    # ------------------------------------------------------------------ #

    def live_entries(self) -> list[AgentEntry]:
        """Non-archived rows — the default ``GET /api/agents`` view."""
        return [e for e in self.entries() if not e.archived]

    def archived_entries(self) -> list[AgentEntry]:
        """Rows marked archived by ``dissolve`` — visible only on explicit ask."""
        return [e for e in self.entries() if e.archived]

    def count_live_events(self) -> int:
        """裁定 19: 存活 events 类代理计数（用于 ≤5 上限护栏）。"""
        return sum(
            1 for e in self.live_entries() if e.category == "events"
        )

    # ------------------------------------------------------------------ #
    # 模型路由
    # ------------------------------------------------------------------ #

    def router(self) -> "ModelRouter":
        """那张**角色 → 模型**的共享表本身；**不把名册行的覆盖折进来**（裁定 172）。

        这里以前会把每一行的 ``provider``/``model`` 直接盖到 ``routes[entry.role]`` 上，
        而现核未归档 24 行里 ``l2_project`` 占 23 行 ⇒ 给 Aura 单选一个模型就是改写
        ``l2_project`` 这一整类（后写的还赢），ADR-0013 完成定义第 1 条
        「给某个 Agent 单独选，只有它变」因此结构上不成立。这一格现在只管「这一类默认用什么」，
        **某一个 Agent 的生效值问 :meth:`route_for`**——惰性 import 的方向问题不变。
        """
        from plobi.routing import ModelRouter

        return ModelRouter.load()

    def route_for(self, entry: AgentEntry) -> "ModelRoute":
        """这一个 Agent 的生效路由＝**那一行自己的** provider/model 盖在它那一类的路由之上。

        裁 172 的口径：覆盖的单位是名册那一行，不是 role。省略那两格 = 没有覆盖 =
        整类的值原样生效；只给一半时另一半仍从类表继承（``entry.provider or 类.provider``），
        但这一步**只改这一行**，绝不写回 ``routes[role]``。
        """
        route = self.router().resolve(entry.role)
        if not entry.has_model_override:
            return route
        from plobi.routing import ModelRoute

        return ModelRoute(
            role=route.role,
            provider=entry.provider or route.provider,
            model=entry.model or route.model,
        )

    def route_problems_for(self, entry: AgentEntry) -> list[str]:
        """ADR-0011 那条 L1≠L2 的断言也要吃**这一行**的生效值。

        折进类表的那个做法顺带让这个 guard 看见了错的东西；拆开之后 guard 不能跟着变瞎，
        所以这里补一格：拿 :meth:`route_for` 的值与 L1 比，同一条 qualified 就是不绿。
        """
        try:
            l1 = self.router().resolve(L1_SECRETARY_ROLE)
        except Exception:
            return []
        try:
            route = self.route_for(entry)
        except Exception:
            return []
        if entry.role == L1_SECRETARY_ROLE or not route.configured or not l1.configured:
            return []
        if route.qualified == l1.qualified:
            # 话术跟着 :meth:`plobi.routing.ModelRouter.violations` 走，不留第二种说法：
            # 那条说的就是 ``"<role> shares L1's model (...)"``。
            return [
                f"{entry.role} shares L1's model ({route.qualified}) "
                f"— {entry.name} 这一行自己选的，不是整类（裁 172）；"
                "the expensive model would end up doing per-message work"
            ]
        return []

    def routing_problems(self) -> list[str]:
        """ADR-0011 断言问题列表（空 = 绿）：**类表那一份 + 每一行自己那一份**。

        裁 172 把每行覆盖从 ``routes[role]`` 上摘下来之后，只看类表会让这条 guard 变瞎
        （名册里给某个项目分身躯一台旗舰，类表上看不见）。所以两类账都数：类表自己的，
        和逐行 :meth:`route_for` 的。
        """
        try:
            problems = self.router().violations()
        except Exception as exc:  # 兜底：缺 L1 等异常当问题上报
            return [f"routing check failed: {exc}"]
        for entry in self.entries():
            problems = problems + self.route_problems_for(entry)
        return problems

    # ------------------------------------------------------------------ #
    # spawn
    # ------------------------------------------------------------------ #

    def spawn(self, name: str) -> dict:
        """把一个注册条目落地为常驻 profile + 模型路由。

        1. **不再建家**（WP-REGISTRY-NO-HOME，B 刀）：只把共享层那一份接好
           （:meth:`_ensure_shared_layer`）。身份走 :func:`ensure_l2_identity_soul`
           写进共享层 `souls/<name>.md`（A 刀），密钥走
           :func:`apply_secret_overrides` 注入子进程环境。
        2. 把该 agent 的路由写进**共享层那一份** ``plobi/models.json``（裁 166 决定一）。
        3. ADR-0011 断言（L1≠L2 模型）必须绿，否则抛 RegistryError。
        4. 返回 dict：profile 路径、启动命令、routing 断言。

        分身家**不写任何配置节**：模型与工作目录由名册那一行在读时叠
        （:func:`profile_config_overlay`），技能根由 :func:`apply_shared_skills_root`
        写进顶层那一份—— ``spawn`` 这一条腿从此不落 ``config.yaml``。
        """
        entry = self.get(name)
        if entry is None:
            raise RegistryError(f"agent {name!r} is not registered")

        # WP-REGISTRY-NO-HOME（B 刀）：**名册加一行不再建家**。身份（A 刀已搬去
        # 共享层 `souls/<name>.md`）、模型路由（`_write_profile_models` 写共享层）、
        # 技能根（`apply_shared_skills_root`）都不需要一个家来承载；会话落单库
        # （裁定 180）。剩下的只有共享层那一份要接好。
        self._ensure_shared_layer(entry)
        # C 刀：新行的工具集真源写进名册（diet_entry_toolsets 幂等）。
        # B 刀摘掉的旧调用是 apply_l2_project_diet(家)——家不再被建，那一式已死。
        try:
            diet_entry_toolsets(entry)
        except Exception as exc:  # 不减也不许挡住 spawn
            logger.debug("plobi: diet (registry row) skipped for %s: %s", entry.name, exc)

        # WP-L2-IDENTITY / 裁定 42 §6(c): 分身缺的是「我是谁」。身份现在落在
        # 共享层那一份（A 刀），**不传 home**——传了就又写回分身家，前功尽弃。
        try:
            ensure_l2_identity_soul(entry)
        except Exception as exc:
            logger.warning(
                "plobi: L2 identity SOUL not written for %s: %s", entry.name, exc
            )

        # 1) 共享层那一份 models.json（ADRD-0011 断言基于它）
        profile_models = self._write_profile_models(None, entry)

        # 2) routing 断言绿（类表那一份 + **这一行**的生效值，裁 172 拆开两处消费方）
        problems = self._profile_routing_problems(profile_models) + self.route_problems_for(entry)
        if problems:
            raise RegistryError(
                f"agent {name!r} routing violates ADR-0011: " + "; ".join(problems)
            )

        # 3) 模型与工作目录**不再往分身家落任何一节**（裁 166 / ADR-0013 ``config.yaml`` 那一行）。
        #    以前这一步是两次写盘：``_write_profile_config`` 写 ``model:``、
        #    ``apply_l2_project_cwd`` 写 ``terminal.cwd:``。两样东西的真源本来就是名册那一行
        #    （``provider``/``model`` 与 ``project_path``），现在由
        #    :func:`profile_config_overlay` 在 :func:`plobi_cli.config.load_config` 里读时叠，
        #    顺序＝「名册行覆盖 → 顶层 → 出厂默认」。落盘那两扇门在这里关掉。
        return {
            "name": name,
            "role": entry.role,
            # **没有 profile_dir**：名册加一行不再落一个家（B 刀）。
            # command 也不再带 `-p`——那是已判废的形状（裁定 42），身份靠
            # `PLOBI_AGENT_ID`（见 `plugins/plobi-north-star/agent_ask.build_child_command`）。
            "command": "plobi chat",
            "routing_ok": True,
        }

    def _ensure_shared_layer(self, entry: AgentEntry) -> Path:
        """把**共享层**那一份接好；**不建分身家**（WP-REGISTRY-NO-HOME，B 刀）。

        原来这里是 ``create_profile(no_skills=True)``：名册加一行就落一个家。
        A 刀把身份搬去共享层之后，家不再承载任何东西——技能 / 配置 / 记忆走共享层、
        模型路由写共享层、会话落单库（裁定 180）——这一步只剩历史惯性，关掉。

        仍保留的只有 :func:`apply_shared_skills_root`：它写**顶层那一份**的
        ``skills.external_dirs``，与家存不存在无关，搬走家也不能丢它。

        返回共享层 root，给还要一个「落点」的调用方（``_write_profile_models`` 的
        参数本就是摆设，它写 :func:`plobi.routing.models.shared_config_path`）。

        显式要一个家的人走 ``plobi -p <name>``：那条入口现在**懒建**空壳
        （``plobi_cli/main.py``），不再 ``sys.exit(1)``。
        """
        from plobi_constants import get_default_plobi_root

        try:
            apply_shared_skills_root()
        except Exception as exc:  # 兜底：共享根写不进去也不该让 spawn 失败
            logger.warning(
                "plobi: shared skills root not wired for %s: %s", entry.name, exc
            )
        return get_default_plobi_root()

    def _write_profile_models(self, profile_dir: Path, entry: AgentEntry) -> Path:
        """把注册表整体路由写进**共享层那一份** `plobi/models.json`（裁定 166 决定一）。

        原来这一句是 ``profile_dir / "plobi" / "models.json"``：每次 spawn 都往那个分身家
        落一份整表复印件——现核 **23 份、去重后内容只有 1 份**，而顶层那一份反倒不存在，
        于是「改一次模型要动 23 个文件」，用户那句「我在设置里配一次大家都能用」就是冲着它说的。
        名字仍叫 ``_write_profile_models``、签名不变：调用方（``spawn`` 与 ADR-0011 那两道断言）
        拿到的还是「这次生效用的那张表」，只是那张表现在只有一份。
        """
        from plobi.routing import ModelRouter
        from plobi.routing.models import shared_config_path

        target = shared_config_path()
        router = self.router()  # 默认 + 全部注册覆盖（名册行覆盖仍然叠在最上层）
        router.save(target)
        return target

    def _profile_routing_problems(self, models_path: Path) -> list[str]:
        from plobi.routing import ModelRouter

        router = ModelRouter.load(models_path)
        return router.violations()


# ---------------------------------------------------------------------------
# 分身的 profile 名能不能在这台机器上落地（R-071 保留名撞车）
# ---------------------------------------------------------------------------


def suggested_profile_name(entry: AgentEntry) -> str:
    """给这条记录推荐一个能登记进 slug 表的运行时 slug（推荐不出来为空串）。

    2026-11-05 之后 slug 不是可填写的一格：这个值唯一的用处是往
    ``runtime-slugs.yaml`` 写一行 ``<name>: <slug>``。候选依次是
    ``name.lower()``（ASCII 名多数直接合法）、``l2-<name.lower()>``、
    ``agent-<name.lower()>``，都要过 ``validate_profile_name`` 与本地派生形状。
    """
    from plobi_cli.agent_identity import validate_profile_name

    base = entry.name.strip().lower()
    for candidate in (base, f"l2-{base}", f"agent-{base}"):
        try:
            validate_profile_name(candidate)
        except ValueError:
            continue
        if derive_runtime_slug(candidate) == candidate:
            return candidate
    return ""


def profile_name_blocker(entry: AgentEntry) -> str:
    """这条记录的 profile 家**永远建不出来**时，返回一句能行动的话（没问题返回空串）。

    判据只有一份：``plobi_cli.agent_identity`` 的 profile 名校验。这里加的只有「改什么」——
    项目分身叫 ``Plobi`` 时小写化就是保留名 ``plobi``（和 Plobi 自己的安装撞），
    以前用户只看到一句 "Profile name 'plobi' is reserved"，不知道要改哪一格，
    于是那个分身永远问不到（R-071 实测第二条）。
    """
    from plobi_cli.agent_identity import validate_profile_name

    from plobi.routing.models import L2_COURSE

    if entry.role == L2_COURSE:
        # 数据条目**本来就没有家**——这不是缺陷，不该报"建不出自己的家"。
        # （它的"问不到"由 :func:`agent_askability` 按角色那句说，不靠这里。）
        return ""

    # 2026-11-06（profile 连根拔）：`entry.profile_name` 这一格已从 AgentEntry 删掉，
    # 身份名只有一个来源——名册 name 派生出的 slug。这里照旧只判那一个派生值。
    canon = derive_runtime_slug(entry.name)
    # 2026-11-05：判据从「名册上显式 profile 缺失」改成「派生 slug 为空或形状不合法」。
    # 派生值为空涵盖三种形状：非 ASCII 名未登记、结果过不了形状规则、撞保留名。
    # **文案一个字不动**——tests/plobi_cli 里有断言钉着这句中文。
    if canon and _RUNTIME_SLUG_RE.match(canon):
        try:
            validate_profile_name(canon)
        except ValueError as exc:
            reason = str(exc)
        else:
            return ""
    else:
        reason = "派生不出可用的运行时名字"
    hint = suggested_profile_name(entry)
    advice = f"给这条记录单独填一个能用的运行时名字，比如 ``{hint}``；" if hint else ""
    return (
        f"智能体「{entry.display_name or entry.name}」起不来：它的运行时名字"
        f" ``{canon}`` 不能用（{reason}）。{advice}改完再登记一次就能问到它了。"
    )


# ---------------------------------------------------------------------------
# 「这一条现在问得到吗」——名册与 Agent 之间那枚嘴共用的唯一判据（R-072）
# ---------------------------------------------------------------------------

#: 只有项目分身接受当面提问。日程 L2 有它自己的沟通路（``say`` / ``ask``，裁定 183），
#: default profile 是提问的那一个。判据看的是**角色**不是名字——``agenda`` 不是
#: 因为叫 ``agenda`` 被排除，任何非项目角色都同样走不到这条路上（裁定 69：
#: 日程秘书与其他 L2 地位平等，两边都不给它开后门、也不给它挂特殊牌子）。
ASKABLE_ROLE = "l2_project"


def has_agent_identity(entry: AgentEntry) -> bool:
    """裁定 192：**身份就是名册这一行本身**——判据是这一行有没有角色路由，不再是盘上有没有一个家。

    `l2_course` 是数据记录，故意不进 ``DEFAULT_ROUTES``（`plobi/routing/models.py:31-38`：没有路由
    就 ``resolve()`` 抛），所以它天然不在这把尺里；`l1_secretary`/`l2_agenda`/`l2_planner`/`l2_project`
    都有路由，就是能对话的对象。``registered`` 那一格章也要盖上——Mind 投影进来的
    占位行（``registered=False``）不是手下，否则「一个能问的人都没有」那种关门判据会失效。

    这一格取代原来那三条"探目录"的判据：`profiles/<name>` 在裁 178 乙案里本来就要消失，
    把"有没有身份"绑在它上面，等于让一次数据搬家把整条左栏打瘫（10-23 11:36 真发生过一次）。
    """
    from plobi.routing.models import DEFAULT_ROUTES

    return bool(entry.registered) and entry.role in DEFAULT_ROUTES


def agent_askability(entry: AgentEntry) -> "tuple[bool, str]":
    """L1 现在能不能用 ``ask`` 问到这一条；问不到就给一句原因。

    只看结构性事实，不看任何一句文本：归档 / 这一行有没有角色路由
    （:func:`has_agent_identity`，裁 192：身份就是名册这一行，不看盘）/ profile 名建不建得出来
    （:func:`profile_name_blocker`）。名单与工具**必须读同一份判据**
    ——以前名单那条路报「有这个人」而那枚嘴报「问不到」，
    两边各判一次，秘书就只能靠试（R-072）。

    **不再探盘**：`profiles/<name>` 在裁 178 乙案里本来就要消失，把"问得到"绑在目录存在上，
    会让一次数据搬家把 L1 的名单清空（10-23 11:36 发生过）。
    """
    if entry.archived:
        return False, "这一条已经归档，问不到它。"
    if entry.role != ASKABLE_ROLE:
        return False, (
            f"这一条的角色是 ``{entry.role}``，不是项目智能体，不归那枚嘴问"
            "（日程秘书有它自己的沟通路：say / ask）。"
        )
    if not has_agent_identity(entry):
        return False, f"这一条的角色是 ``{entry.role}``，是数据记录、没有路由，问不到。"
    blocker = profile_name_blocker(entry)
    if blocker:
        return False, blocker
    return True, ""


def is_askable_agent(entry: AgentEntry) -> bool:
    """:func:`agent_askability` 的布尔口径（只要「能不能」，不要原因）。"""
    return agent_askability(entry)[0]


def agent_navigability(entry: AgentEntry) -> "tuple[bool, str]":
    """桌面左栏这一行能不能点进聊天页；点不了就给一句原因。

    和 :func:`agent_askability` 是**两件事**，别混：askability 判的是 Agent 之间
    那枚嘴（裁定 183 之后是 ``ask``，内壳 ``agent_ask``）问不问得到——日程秘书
    走它自己的沟通路，所以 agenda 行 askable=False 却完全点得动；
    navigability 判的是「点了之后桌面能不能真的拉起一个后端来对话」——
    也就是 spawn 不会失败。这正是裁定 146②(b) 那句「不许列在那儿点不动」的
    可执行形状。三条否决，全是结构性事实：

    1. 这一行**没有角色路由**（:func:`has_agent_identity`，裁 192）——课程数据条目
       （``l2_course`` 故意不进 ``DEFAULT_ROUTES``）没有运行时可连，点进去只会是一张空壳。
       有身份的课程行（``l2_project`` + ``category: courses``，裁定 143②）有路由，照样可点；
    2. ``profile_name_blocker`` 非空——保留名（名册里那条 ``Plobi`` 行的
       ``l2-plobi``，小写化撞上安装本身）这类永远建不出家的形状，点了就是一次
       必然失败的 spawn。

    **原来还有第 3 条「家在不在盘上」，10-23 已删**：那条把"能不能点"绑在
    ``profiles/<name>`` 存在上，而裁 178 乙案的终态就是这个目录不存在——
    11:36 一次整屋搬家因此把 24 行左栏全部打成点不动（事故，已回滚）。
    """
    if entry.archived:
        return False, "这一条已经归档。"
    if not has_agent_identity(entry):
        return False, "这一条没有自己的运行时（课程是数据记录，不是可对话的智能体）——点进去也只会是一张空壳。"
    blocker = profile_name_blocker(entry)
    if blocker:
        return False, blocker
    return True, ""


def _normalized_project_name(entry: AgentEntry) -> str:
    """这一条主张的「项目名」归一形：``display_name`` 优先，空时回退注册键。"""
    return (entry.display_name or entry.name or "").strip().casefold()


def registration_blocker(registry: AgentRegistry, entry: AgentEntry) -> str:
    """这一条**不该进名册**时，说清它撞了哪一条、该改什么（没问题返回空串）。

    用户 2026-10-06 的要求：重名在**创建那一刻**就禁止，而不是等 ``upsert`` 兜。
    判据只有这一份，创建门（``POST /api/agents``）调它，桌面弹窗不另算一套——它只
    负责把这里的话显示出来。:meth:`AgentRegistry.upsert` 里那条大小写闸门留着，
    是给 CLI / Mind 播种机兜底的，两处共用 :func:`_case_twin_message` 同一句措辞。

    存量那对 ``Nymo``/``nymo`` 不在射程内：本函数只拦**新增**。改已有条目（键相同、
    名字也还是它自己的）照样放行——真实数据只读，不替用户合并也不替他删。
    """
    held = registry.get(entry.name)
    creating = held is None

    if creating:
        twin = registry.find_case_twin(entry.name)
        if twin:
            return _case_twin_message(entry.name, twin, entry.profile_name)

    subtree = (entry.mind_subtree or "").strip()
    if subtree:
        owner = registry.find_mind_subtree_owner(subtree, exclude=entry.name)
        if owner:
            return (
                f"Mind 那棵树（``{subtree}``）已经挂在智能体「{owner}」名下——同一个项目不登记"
                f"第二条：要推进它就是去编辑「{owner}」，真另起一个项目请换一个项目名。"
            )

    incoming = _normalized_project_name(entry)
    if creating:
        for other in registry.live_entries():
            if other.name == entry.name or not incoming:
                continue
            if incoming == _normalized_project_name(other):
                return (
                    f"名册里已经有一条叫「{other.display_name or other.name}」的智能体"
                    f"（注册键 ``{other.name}``）——重名不许再建第二条：要改它就是编辑那一条，"
                    f"想真的另起炉灶请换一个项目名。"
                )
        return ""

    # 命中已有键、但这条槽位属于另一个项目名：那是「顶替」，不是「编辑」。
    held_name = _normalized_project_name(held)
    if incoming and held_name and incoming != held_name:
        return (
            f"注册键「{entry.name}」已经是智能体「{held.display_name or held.name}」——"
            f"新建请换一个名字；要改那一条，请带着它自己的名字来编辑它。"
        )
    return ""


# 什么算「共享凭据」：家目录 `.env` 里这些后缀的项。名单按后缀而不按具体 provider，
# 是为了不再造一份 provider 真源（那是裁定 81 刚清掉的东西）。
SHARED_SECRET_SUFFIXES = ("_API_KEY", "_KEY", "_TOKEN", "_SECRET")


def home_shared_secrets() -> dict[str, str]:
    """家目录 ``.env`` 里的共享凭据 —— 一分身一份拷贝的话，真源就这一处。

    调用方随后把子进程的 ``PLOBI_HOME`` 指向 profile 家，届时子进程读到的 ``.env``
    是那个家自己的文件；裁定 42 §42.2 明令那里不留第二份密钥，可早先建的家确实
    留过（实测 11 个分身的家是同一把旧 key 的拷贝）。所以共享的那几把**在这里**
    读出来、注进去，而不是指望分身自己去继承。
    """
    try:
        from plobi_cli.config import credential_env_path, load_env

        entries = load_env(credential_env_path()) or {}
    except Exception:
        return {}
    return {
        str(name): str(value)
        for name, value in entries.items()
        if value and str(name).upper().endswith(SHARED_SECRET_SUFFIXES)
    }


def apply_secret_overrides(env: "dict[str, str]", entry: AgentEntry) -> None:
    """把这条记录声明的密钥覆盖注进一份子进程环境（就地改，不落盘）。

    裁定 42 §42.2 第 6 项允许按项目不同的三样里，密钥这一样以前靠整份拷根
    ``.env`` 实现（实测 13 个分身一把自己的密钥都没有）。分身不再拷家之后，共享的
    那部分密钥仍由继承来的进程环境提供，只有这里声明的几条是**这个项目的覆盖**；
    写进 profile 的 ``.env`` / ``config.yaml`` 等于又造出一份复印件，故禁止。

    顺序（本刀定的唯一顺序）：**家目录的共享凭据 < 这条记录声明的覆盖**，
    两者都进子进程环境并登记进 ``PLOBI_INJECTED_SECRETS``——那份登记让它们在
    子进程里赢过它自己家里的 ``.env`` 旧拷贝（:func:`plobi_cli.config
    .get_env_value_prefer_dotenv`）。不登记就会出这种局面：秘书自己认证得过，
    它问出去的每一句都 ``HTTP 401``，而盘上没有任何一行说明用的是两把不同的 key。
    """
    merged = dict(home_shared_secrets())
    merged.update(
        {
            str(key): str(value)
            for key, value in (entry.secret_overrides or {}).items()
            if key and value
        }
    )
    if not merged:
        return

    from plobi_cli.config import INJECTED_SECRETS_ENV

    for name, value in merged.items():
        env[name] = value
    already = {n.strip() for n in (env.get(INJECTED_SECRETS_ENV) or "").split(",") if n.strip()}
    env[INJECTED_SECRETS_ENV] = ",".join(sorted(already | set(merged)))


# ---------------------------------------------------------------------------
# 可解析闸门：这个 provider 名字底座认不认
# ---------------------------------------------------------------------------


def provider_is_resolvable(provider: str) -> bool:
    """问底座那个唯一权威：``provider`` 这个名字运行时解析得出来吗？

    权威 = :func:`plobi_cli.auth.resolve_provider`（``plobi_cli/auth.py:1625``）——
    也就是桌面上那条「Unknown provider '<x>'. Check 'plobi model' …」红条的出处
    （``plobi_cli/auth.py:1704``，抛 ``AuthError(code="invalid_provider")``）。
    只有它能判，因为它一个函数里做完了别名归一（``glm`` → ``zai``、
    ``moonshot`` → ``kimi-coding``）、插件 provider 发现（``PROVIDER_REGISTRY`` 在
    ``plobi_cli/auth.py:447`` 起被 ``plugins/model-providers/`` 扩展）以及 ``custom``
    / ``openrouter`` 特例。本层**不抄一份 provider 名单**（那是第二真源，插件装掉
    一个 provider 我们就判错），也只问不判。

    拿不到权威时按**能写**处理（fail-open）：import 不了 plobi_cli（本层刻意不在
    模块级依赖它，见 :meth:`AgentRegistry._ensure_shared_layer` 的惰性 import 与 51ff7f0
    的教训）不等于权威说了"不行"；一道闸门不许把注册/派生流程弄失败。
    """
    name = (provider or "").strip()
    if not name:
        return True
    try:
        from plobi_cli.auth import resolve_provider
    except Exception as exc:  # pragma: no cover — 环境缺底座时不拦
        logger.debug(
            "plobi: provider resolver unavailable (%s); model write allowed", exc
        )
        return True
    try:
        resolve_provider(name)
    except Exception as exc:
        code = getattr(exc, "code", None)
        if code == "invalid_provider":
            return False
        # 解析器自己出错（读配置炸了等）≠ 这个名字不可解析。别拿它当否决票。
        logger.debug(
            "plobi: provider resolver errored for %r (%s); model write allowed",
            name,
            exc,
        )
        return True
    return True


# ---------------------------------------------------------------------------
# 名册行的读时配置覆盖（裁 42.2 允许的那三项里，落在这层的有两项）
# ---------------------------------------------------------------------------


def entry_for_profile(profile_name: str) -> Optional[AgentEntry]:
    """按**已落地的家目录名**反查名册那一行；查不到给 None。

    反查而不是正查是必须的：配置读侧只知道活跃家是哪个目录（``profiles/<name>``），
    而 ``AgentEntry.profile_name`` 是归一小写过的（见那个属性自己的说明），所以这里
    也按 casefold 比。归档行不算——它的家已经不在 ``profiles/`` 下了。
    """
    found = _registry_and_entry_for_profile(profile_name)
    return found[1] if found is not None else None


def identity_name_in_entries(entries, key: str | None) -> str:
    """在**手里这一批名册行**里认出「这个写法是谁」；认不出回空串。

    与 :func:`identity_name_for_key` 同一套判据（名册 name、它的小写、旧家名、
    ``souls/`` 里那个身份文件名、记忆那颗 slug），区别只在**不重新读盘**：调用方
    手里常常已经有一份 registry 实例（``plobi/console`` 的请求面就是——它自己维护
    「文件变了才重读」的缓存，这里再 parse 一遍 yaml 等于把每次请求的钱翻倍）。

    判据只有一条：认得出就回那一行的 ``name``，认不出就回空串——空串的含义是
    「没带身份」或「名册里真没这一行」，**不是**「这是 L1」。谁把它当 L1 用，
    谁就得在调用点上明说（console 那一跳的注释就写了这件事）。
    """
    raw = (key or "").strip()
    if not raw:
        return ""
    try:
        from tools.memory_tool import resolve_agent_slug
    except Exception:  # noqa: BLE001 - 记忆那条腿不可用就少一种写法
        resolve_agent_slug = None
    for entry in entries:
        if getattr(entry, "archived", False):
            continue
        candidates = {entry.name, entry.name.lower()}
        value = str(getattr(entry, "profile_name", "") or "").strip()
        if value:
            candidates.add(value)
            candidates.add(value.lower())
        try:
            candidates.add(shared_soul_path_for(entry.name).stem)
        except Exception:  # noqa: BLE001 - 算不出文件名就少一种写法
            pass
        if resolve_agent_slug is not None:
            try:
                candidates.add(resolve_agent_slug(entry.name))
            except Exception:  # noqa: BLE001
                pass
        if raw in candidates:
            return entry.name
    return ""


def agent_id_for_scope(scope: str | None, *, entries=None) -> Optional[str]:
    """作用域／身份 → 总账的行级键 ``sessions.agent_id``。三个返回值三种意思。

    * ``None`` —— **不圈行**：作用域是 ``all``（跨 Agent 的全局面），或名册里认不出这写法。
    * ``''``   —— **L1 那一格**。裁定 180 把秘书的归属键定成空串；名册里 L1 那一行
      （``role=l1_secretary``，这台它的 slug 恰好叫 ``default``）**不是**另一个键，
      它是同一格的另一种写法。**没带作用域也是这一格**——与 SINGLE-GATEWAY §3.4
      「空 = 进程默认身份（L1 总秘书）」同形，桌面刻意不维护第二种"没说"的状态。
    * 其余     —— 名册那一行的 ``name``（L2 的行级键）。

    为什么单独立一只口：2026-11-05 那次「我和总秘书的对话又消失了」是两个错叠在
    一起——① 读面把解析结果 ``or None``，于是 L1 与"全局"成了同一件事；② 名册进了
    L1 行之后 ``default`` 被翻成 ``secretary``，圈出来 0 行（真库现读：不圈 193 行、
    按 ``secretary`` 圈 0 行、按空串圈才是秘书那一格）。**L1 不代表全局**，所以这件事
    要在一只口里判一次，不许每个读面各自猜。
    """
    raw = (scope or "").strip()
    if not raw:
        return ""
    if raw.casefold() == "all":
        return None
    pool = list(entries) if entries is not None else list(load_registry().entries())
    name = identity_name_in_entries(pool, raw)
    if not name:
        # 认不出：不圈。存在性另有 `_scope_name_for_read` 那道 404，这里不重复判。
        return None
    entry = next((e for e in pool if e.name == name), None)
    if entry is not None and getattr(entry, "role", "") == L1_SECRETARY_ROLE:
        return ""
    return name


def _registry_and_entry_for_profile(profile_name: str):
    """同 :func:`entry_for_profile`，但把那份注册表一起带回来（省一次读盘）。"""
    want = (profile_name or "").strip().casefold()
    if not want:
        return None
    try:
        registry = AgentRegistry.load()
    except Exception:
        return None
    entry = _entry_for_profile_in(registry.entries(), want)
    return (registry, entry) if entry is not None else None


def _entry_for_profile_in(entries: list[AgentEntry], want: str) -> Optional[AgentEntry]:
    """作用域值 → 名册那一行；**指代不唯一就给 None**（2026-11-05）。

    11-05 之前这里是"谁先撞上就返回谁"：``profile_name`` 是本行派生的 slug、
    ``name`` 是身份键，两个口径混在同一个 casefold 空间里比较，一旦两行撞车
    （``Nymo`` / ``nymo`` 这种存量对，或某行的 slug 恰好等于另一行的 name），
    返回哪一行取决于名册里的行序——那不是身份，那是抽签。

    现在的判据：先按 ``profile_name``（slug）找，**只认唯一命中**；slug 一个都
    不中再按 ``name`` 找，同样只认唯一命中。两轮都撞车 ⇒ None。调用方拿 None
    的表现与"名册里没有这一行"完全一致（404 / 不进桶），宁可说不认识，也不
    把会话挂到别人的家里去。
    """
    alive = [e for e in entries if not e.archived]

    by_slug = [e for e in alive if (e.profile_name or "").casefold() == want]
    if len(by_slug) == 1:
        return by_slug[0]
    if len(by_slug) > 1:
        return None
    # C 刀：``PLOBI_AGENT_ID`` 带的是**名册 name**（裁定 180 的身份），不一定是
    # profile 名——一行可以 ``name: Stithy`` 而 ``profile: l2-stithy``。老形状
    # （家在 profiles/ 下）走上面那段；新形状（共享层 + AGENT_ID）走这段。
    by_name = [e for e in alive if e.name.casefold() == want]
    if len(by_name) == 1:
        return by_name[0]
    return None


def project_cwd_for_profile(profile_name: str) -> Optional[str]:
    """这个家的 ``terminal.cwd``＝名册那一行的主文件夹；**目录不存在就不给**。

    以前这一样是 ``apply_l2_project_cwd`` 往分身家 ``config.yaml`` 写一节实现的，
    于是每建一个项目分身就多一份复印件。真源本来就是名册那一行的 ``project_path``
    （R-013 的文件夹绑定），读时算即可，不需要那份文件。
    宁可不给 cwd，也不把一个假路径塞进会话的工作目录——那是 ``apply_l2_project_cwd``
    当年就写死的判据（「NEVER fabricate a cwd」）。
    """
    entry = entry_for_profile(profile_name)
    if entry is None:
        return None
    return _resolvable_project_cwd(entry)


def _resolvable_project_cwd(entry: AgentEntry) -> Optional[str]:
    raw = str(entry.project_path or "").strip()
    if not raw:
        return None
    try:
        resolved = Path(raw).expanduser().absolute()
    except (OSError, RuntimeError):
        return None
    return str(resolved) if resolved.is_dir() else None


def profile_config_overlay(profile_name: str) -> dict:
    """这个家的**名册行读时配置覆盖**，形状＝``config.yaml`` 里那两节。

    被 :func:`plobi_cli.config.agent_config_overlay` 调用（那条链是
    「名册行覆盖 → 顶层 → 出厂默认」，ADR-0013 ``config.yaml`` 那一行的目标形状）。
    两项覆盖以前各有一份落盘实现（``_write_profile_config`` 写 ``model:``、
    ``apply_l2_project_cwd`` 写 ``terminal.cwd:``），两扇落盘的门在本刀一起关掉，
    判据与 :func:`apply_secret_overrides` 同一句：**就地叠，不落盘成第二份**。

    模型那一项的两道闸原样从 ``_write_profile_config`` 搬过来，一条不放宽：
    ① 只有那一行**真的声明了** ``provider``/``model`` 才给（省略 = 没有覆盖；不许把
       :data:`DEFAULT_ROUTES` 那套硬编码当成这个分身自己的选择盖下去）；
    ② 名字底座解析不出来（:func:`provider_is_resolvable`）时**整节都不给**——给一个
       解析不出来的 provider = 这个分身一启动就一条 ``Unknown provider`` 红条。

    ⚠️ 裁定 172 那句「覆盖必须按 **Agent 身份**解析，不许折进共享 role 路由」在这里是硬约束，
    所以这一格拿 :meth:`AgentRegistry.route_for`——那一行自己的 ``provider``/``model`` 盖在
    它那一类的路由之上，**绝不写回** ``routes[role]``（现核未归档 24 行里 ``l2_project`` 占 23 行，
    折进类表就等于给 Aura 单选一次、22 个一起变）。
    """
    found = _registry_and_entry_for_profile(profile_name)
    if found is None:
        return {}
    registry, entry = found
    overlay: dict = {}
    if entry.has_model_override:
        try:
            route = registry.route_for(entry)
            provider = (route.provider or "").strip()
            model = route.model or ""
        except Exception:
            provider = ""
            model = ""
        if provider:
            if not provider_is_resolvable(provider):
                # 那句点名的 warning 是从 ``_write_profile_config`` 原样搬来的：闸还是那道闸，
                # 但"为什么这个分身没吃到你给它选的模型"必须说得出名字，不能静默留空。
                logger.warning(
                    "plobi: model override NOT applied for %r — the runtime provider "
                    "resolver does not know provider %r (model %r), and applying it would "
                    "make this 分身 fail with \"Unknown provider '%s'\" on every start. "
                    "Skipping keeps it on the model the shared config declares. Run "
                    "'plobi model' for the list of resolvable providers or 'plobi doctor' "
                    "to diagnose.",
                    entry.name,
                    provider,
                    model,
                    provider,
                )
            else:
                section = {
                    k: v for k, v in (("provider", provider), ("default", model)) if v
                }
                if section:
                    overlay["model"] = section
    cwd = _resolvable_project_cwd(entry)
    if cwd:
        overlay["terminal"] = {"cwd": cwd}
    # C 刀：三份 per-agent 黑名单也走 overlay（裁定 42 §42.2 的"可选覆盖"延伸到
    # 减法侧）。消费点是 ``plobi_cli/tools_config.py`` 与 ``agent/skill_utils.py``
    # 里既有的 ``agent.disabled_toolsets`` / ``disabled_skills`` /
    # ``disabled_mcp_servers``——不新开读取路径。**并集**在 config.py 那一侧做
    # （overlay 不知道顶层当时有什么，替换会吃掉用户自己的选择）。
    disabled: dict = {}
    if entry.disabled_toolsets:
        disabled["disabled_toolsets"] = [str(n) for n in entry.disabled_toolsets]
    if entry.disabled_skills:
        disabled["disabled_skills"] = [str(n) for n in entry.disabled_skills]
    if entry.disabled_mcp_servers:
        disabled["disabled_mcp_servers"] = [str(n) for n in entry.disabled_mcp_servers]
    if disabled:
        overlay["agent"] = disabled
    return overlay


def _active_agent_profile_name() -> str:
    """当前进程正在替哪个 Agent 干活；不是分身会话 → ''。

    身份通道（2026-10-24 C 刀起，裁定 180 的行级身份）：

    1. **``PLOBI_AGENT_ID`` 优先**——B 刀（WP-REGISTRY-NO-HOME）之后 L2 分身
       不带 ``-p`` 起来，``PLOBI_HOME`` 是**共享层 root**，家那条判据对它永远
       为假 ⇒ per-agent 的模型 / cwd / 工具集黑名单整条链失效（连
       ``agent_config_overlay`` 开头那道「共享根即空」的门都过不去）。
       名册 name 才是裁定 180 说的身份，它由
       :func:`plobi.agents.registry` 的调用方（``build_child_command``）注入。
    2. 回退到家：``PLOBI_HOME`` 在 ``profiles/`` 底下 = 老形状的分身会话
       （装机链上还有这种进程，回退保证它们逐字不变）。
    3. 都不是 ⇒ ''（L1 / flat，没有 per-agent 偏好可言）。
    """
    agent_id = (os.environ.get("PLOBI_AGENT_ID") or "").strip()
    if agent_id:
        return agent_id
    # 桌面 / api-server 会话的进程 env 没有 PLOBI_AGENT_ID（那是 spawn 出去的子会话
    # 才有的）；会话级身份由前端开 Agent 会话时经 api_server 写入
    # ``gateway/session_context`` 的 ``PLOBI_AGENT_ID`` ContextVar 透传到这里。
    # 有它才谈得上 per-agent 黑名单 / 模型 / cwd 覆盖（P1 审计：桌面正路此前全落空）。
    try:
        from gateway.session_context import get_session_env

        agent_id = get_session_env("PLOBI_AGENT_ID")
    except Exception:
        agent_id = ""
    agent_id = (agent_id or "").strip()
    if agent_id:
        return agent_id
    try:
        from plobi_constants import get_plobi_home

        home = get_plobi_home()
    except Exception:
        return ""
    if home.parent.name != "profiles":
        return ""
    return home.name


def agent_disabled_sets(profile_name: str = "") -> dict:
    """这一个 Agent 的三份工具黑名单；不是分身 / 查无此行 → 全空。

    只读名册一行，**不碰** ``load_config``（本函数会被 config 消费链上的代码
    调用，而名册行覆盖是在 ``_load_config_impl`` 内部叠的——再进去就是递归）。
    名单存的是 skill / toolset / MCP 服务器名，消费方自行做大小写归一。
    """
    want = (profile_name or _active_agent_profile_name()).strip().casefold()
    if not want:
        return {"skills": set(), "toolsets": set(), "mcp_servers": set()}
    found = _registry_and_entry_for_profile(want)
    if found is None:
        return {"skills": set(), "toolsets": set(), "mcp_servers": set()}
    entry = found[1]
    return {
        "skills": {s.casefold() for s in entry.disabled_skills},
        "toolsets": {s.casefold() for s in entry.disabled_toolsets},
        "mcp_servers": {s.casefold() for s in entry.disabled_mcp_servers},
    }


def set_profile_model_override(profile_name: str, provider: str, model: str) -> str:
    """把这一个 Agent 的模型写成**名册那一行的覆盖**（不写任何分身家的配置文件）。

    桌面「给这个 Agent 单独选一个模型」那条腿以前是 ``set_plobi_home_override(那个家)``
    + ``save_config``（``plobi_cli/web_server.py:_write_profile_model``）——在那之前每个分身
    各有一份 ``config.yaml``，写它只改这一个分身。读链收成「名册行覆盖 → 顶层 → 出厂默认」
    之后，那套做法会把**这一个** Agent 的选择写进**那唯一一份**配置（全员一起变，还把
    merge 出来的默认值刷进用户的顶层文件），所以写入改道到名册这一行；生效值由
    :func:`profile_config_overlay` 读时叠，顺序正好是裁 166 决定二那句「覆盖只写名册行」。

    查不到名册行时抛 :class:`RegistryError`：那个家没有地方可以放"只属于它的覆盖"，
    而把它塞进顶层那一份 = 串味，比报错更糟。
    """
    found = _registry_and_entry_for_profile(profile_name)
    if found is None:
        raise RegistryError(
            f"profile {profile_name!r} 没有对应的名册行，无法只给它一个模型"
            "（配置只有一份，写顶层 = 所有 Agent 一起变）。请在 Agent 名册里操作这一条。"
        )
    registry, entry = found
    from dataclasses import replace

    registry.upsert(replace(entry, provider=provider or "", model=model or ""))
    registry.save()
    return entry.name


# ---------------------------------------------------------------------------
# S2: find or spawn exactly one agenda L2
# ---------------------------------------------------------------------------

AGENDA_TEMPLATE_NAME = "agenda"
# 日程 L2 的运行时 slug（内部标识）。2026-11-05 之后它**不再写进条目**
# （``AgentEntry.profile`` 已删除），slug 由 name ``agenda`` 经
# ``runtime_slugs.yaml`` 派生成 ``l2-agenda``。常量留给测试与需要这个名字的
# 调用方对齐。
AGENDA_TEMPLATE_PROFILE = "l2-agenda"
AGENDA_TEMPLATE_ROLE = "l2_agenda"

_AGENDA_ROLE_MARKERS = ("l2_agenda", "secretary-agenda")


def agenda_template_entry() -> AgentEntry:
    """The only L2 ``spawn`` is allowed to create for §8.2 S2.

    这里原来硬写着 ``provider="aigw", model="workbuddy/deepseek-chat"``。那是
    :data:`plobi.routing.models.DEFAULT_ROUTES` 的 L2 兜底抄进了**记录本身**：
    一旦覆盖写在记录上，那一格就会被写进日程分身的 ``config.yaml``：
    ``model.provider: aigw``（2026-09-29 实测他的 l2-agenda 那条一度就是这样，被手工
    纠回 minimax-cn），而 provider 值会成为选择器的分组标题——裁定 45.4 要保留的
    是**日志与配置里机器读的 id**，不是替用户决定他没用过哪个提供商。日程 L2 的
    路由账本（profile 的 plobi/models.json）照旧从 DEFAULT_ROUTES 取，不带上屏。
    """
    return AgentEntry(
        name=AGENDA_TEMPLATE_NAME,
        role=AGENDA_TEMPLATE_ROLE,
        mind_subtree="Dossiers/projects/Plobi",
        skills=("plobi-l2-resident",),
        description="日程采集 → SQLite → 看板 → 钉钉 闭环",
    )


def is_agenda_entry(entry: AgentEntry) -> bool:
    """True when this registry row is the agenda / 日程 secretary.

    Matches existing ``role`` / description / template names. Does not invent
    a second registry — callers still go through :class:`AgentRegistry`.
    """
    role = (entry.role or "").strip().lower()
    name = (entry.name or "").strip().lower()
    profile = entry.profile_name.strip().lower()
    desc = entry.description or ""
    if any(marker in role for marker in _AGENDA_ROLE_MARKERS):
        return True
    if "日程" in (entry.role or "") or "日程" in desc or "日程" in (entry.name or ""):
        return True
    if "secretary-agenda" in name or "secretary-agenda" in profile:
        return True
    return False


def find_agenda_agent(registry: AgentRegistry | None = None) -> Optional[AgentEntry]:
    """The **live** agenda L2 row, preferring an exact ``l2_agenda`` role.

    裁定 211：归档 = 退役。已归档的那行**不再占着槽位**——旧形状它照样算，
    于是用户归档掉产品替他播种的那条之后，连**自己**再建一条都建不成
    （``POST /api/agents`` 返 409「agenda L2 already exists」，撞的是一条
    已经退役的行）。
    """
    reg = registry or load_registry()
    found = [entry for entry in reg.live_entries() if is_agenda_entry(entry)]
    if not found:
        return None
    found.sort(key=lambda entry: (0 if entry.role == AGENDA_TEMPLATE_ROLE else 1, entry.name))
    return found[0]


def register_agenda_agent(registry: AgentRegistry | None = None) -> tuple[AgentEntry, bool]:
    """Put the agenda L2 row in ``projects.yaml`` — and write nothing else.

    The left rail's ``GET /api/agents`` reads that one YAML file, so a visible
    日程管家 needs the row, not the profile. Materializing ``profiles/l2-agenda``
    is a separate promise (a directory, ``models.json``, a config), and a boot
    that makes it on a home whose secretary never ran has built a profile the
    user never asked for. ``spawned`` is True only when this call registered
    the template; idempotent, and never touches the filesystem below the YAML.
    """
    reg = registry or load_registry()
    existing = find_agenda_agent(reg)
    if existing is not None:
        return existing, False

    # 退役的那条**占着名字**（归档 = 退役，不是删除）。旧形状走到这里会另建一条
    # ``secretary-agenda``，于是名册里出现两行、都显示「日程秘书」（左栏短名表
    # 两个 id 映射到同一个词）——用户看着像「又冒出来一个」。
    # 这里改成**复活**那一条：同一行、同一个身份文件，退役的那位回来上班。
    from dataclasses import replace as _replace

    retired = next((e for e in reg.entries() if e.archived and is_agenda_entry(e)), None)
    if retired is not None:
        revived = _replace(retired, archived=False, archived_at="")
        reg.upsert(revived)
        reg.save()
        return revived, False

    template = agenda_template_entry()
    if template.name in reg.agents:
        template = AgentEntry(
            name="secretary-agenda",
            role=template.role,
            provider=template.provider,
            model=template.model,
            mind_subtree=template.mind_subtree,
            skills=template.skills,
            description=template.description,
        )
    reg.upsert(template)
    reg.save()
    return template, True


# ---------------------------------------------------------------------------
# WP-PROJECT-PORTFOLIO: ensure_mind_project_agents（名册按 Mind 项目树补齐）
# ---------------------------------------------------------------------------

# Hard-coded binding for the live workspace (no env lookup, no index scan).
# The Mind INDEX records ``cloud: D:\Cloud\Projects\Plobi``, but the *source*
# copy lives under ``D:/Projects/Plobi/Code`` (task: "Plobi 固定
# ``D:\Projects\Plobi\Code``"). Anything else gets the cloud path from the
# dossier profile's ``registry.cloud`` or, failing that, ``D:\Cloud\Projects\<name>``.
PLOBI_PROJECT_PATH = r"D:\Projects\Plobi\Code"
DEFAULT_CLOUD_PROJECTS_ROOT = r"D:\Cloud\Projects"


def _project_safe_id(raw: str) -> str:
    """Project dir name -> plobi profile-safe id (``[a-zA-Z0-9_-]+``).

    Falls back to a hex digest when the dir name is entirely punctuation, so
    the registry never carries an invalid identifier. The original raw name is
    preserved separately on the entry's ``description`` field.
    """
    import re

    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "-", (raw or "").strip()).strip("-")
    if cleaned:
        return cleaned
    import hashlib

    digest = hashlib.sha1((raw or "").encode("utf-8")).hexdigest()[:12]
    return f"proj-{digest}"


def _read_plan_frontmatter(plan_path: Path) -> dict[str, str]:
    """Parse the YAML frontmatter block at the head of ``plan.md``.

    Returns an empty dict when the file is missing, unreadable, or has no
    frontmatter. Never raises — the project scanner treats unknown plans as
    in-progress and falls back to the default category.

    **迁移期兜底**：``plan.md`` 那一对 11-09 退役了（WP-DOSSIER-SHAPE），真源是档案本体
    （``dossier.json``）的 ``registry``。这一段只留给还没抽成结构化的老档案。
    """
    try:
        text = plan_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {}
    if not text.startswith("---"):
        return {}
    try:
        # yaml.safe_load tolerates a trailing document; we explicitly slice
        # the first fenced block to avoid mixing body prose into the dict.
        end = text.index("\n---", 3)
    except ValueError:
        return {}
    block = text[3:end].strip()
    if not block:
        return {}
    try:
        import yaml

        parsed = yaml.safe_load(block)
    except Exception:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {str(key): ("" if value is None else str(value)) for key, value in parsed.items()}


def _read_project_registry(entry_dir: Path) -> dict[str, str]:
    """一棵树的身份那几格（status／group／cloud…）。

    真源是**档案本体**（``dossier.json``）里的 ``registry``；``plan.md`` 那一对
    11-09 退役（WP-DOSSIER-SHAPE）。迁移期兜底：本体还不存在的老档案仍按它自己的
    frontmatter 读——抽完 md 就只剩本体这一条路。
    """
    from plobi.mind.dossier_profile import profile_path, read_profile

    profile = read_profile(profile_path(entry_dir)).profile
    if profile is not None:
        return {str(key): str(value) for key, value in profile.registry.items()}
    return _read_plan_frontmatter(entry_dir / "plan.md")


def _project_is_skipped(front: dict[str, str]) -> bool:
    """True when the project's dossier profile marks it paused / done / archived.

    Looks at the ``registry.state`` field first (canonical), then falls
    back to a substring scan over the rendered body when frontmatter is
    absent or ambiguous.
    """
    status = (front.get("status") or front.get("state") or "").strip().lower()
    if status and status in PROJECT_SKIP_STATUS_MARKERS:
        return True
    return False


def _project_category(front: dict[str, str], default: str = "projects") -> str:
    """Map a project's INDEX ``group`` to an L2 ``category``.

    Only ``group=research`` flips to ``research``; everything else stays in
    the default catch-all. Never invents a new grade — unknown groups fold
    into ``default`` rather than raising.
    """
    group = (front.get("group") or "").strip().lower()
    if group == "research":
        return "research"
    return default


def _resolve_project_path(name: str, front: dict[str, str]) -> str:
    r"""Pick the ``project_path`` for one project.

    Plobi is always ``D:\Projects\Plobi\Code`` (task rule). Other projects
    prefer the Cloud path already declared in the dossier profile
    (``registry.cloud``); if it is missing, fall back to ``D:\Cloud\Projects\<name>``. The
    ``registry.upsert`` auto-seed is bypassed because we pass ``project_path``
    explicitly below.
    """
    if name == "Plobi":
        return PLOBI_PROJECT_PATH
    cloud = (front.get("cloud") or "").strip()
    if cloud:
        return cloud
    return f"{DEFAULT_CLOUD_PROJECTS_ROOT}\\{name}"


# 播种机给每条**投影**（``registered=False``）填的占位描述。它只是「这行是谁写的」
# 的账面备注，**不是名册判据**：谁算手下只看 :attr:`AgentEntry.registered` 那一格
# （R-072 拆掉的就是「靠这句话长什么样猜手下」），能不能当面问只看
# :func:`agent_askability` 那四条结构性事实——两处都不读 ``description``。
#
# 这句桩话**唯一**的用途是渲染：分身那段身份里「这个项目在做的事」不能印机器写的
# 备注（实测会把 ``l2_project for Mind project Aura`` 当用途念出来）。所以「生成桩」
# 和「识别桩」共用下面这一份常量——识别只在 :func:`build_l2_identity_soul_block` 里
# 用，别拿它当任何判据。
L2_PLACEHOLDER_DESCRIPTION_PREFIX = "l2_project for Mind project "


def _is_placeholder_project_description(description: str) -> bool:
    """True when ``description`` is the seed stub, not a written project purpose.

    **只服务渲染**：这一句判断的是「这行文字是不是播种机自己写的备注」，所以它不进
    名册成员资格（:attr:`AgentEntry.registered`）、也不进可问性判据
    （:func:`agent_askability`）——那两处读的是盘上的事实，一个字都不看 ``description``
    （R-072；:mod:`tests.plobi.test_l1_roster_source` 钉的就是这条边界）。

    Single source of truth: :data:`L2_PLACEHOLDER_DESCRIPTION_PREFIX` — the exact
    string :func:`ensure_mind_project_agents` writes (the `` (raw dir name: …)``
    variant still starts with it, so one prefix check covers both shapes).
    """
    return (description or "").strip().startswith(L2_PLACEHOLDER_DESCRIPTION_PREFIX)


def _mind_project_trees(root: Path) -> list[tuple[str, Path]]:
    """每棵项目树连同它所在的分类文件夹（``(逻辑相对路径, 目录)``）。

    树住在哪个文件夹由 L2 分类决定，见 :mod:`plobi.mind.project_tree`——所以扫描面是那
    五个分类文件夹，不是只有 ``projects/``。只扫一个会读不到搬了家的树（``research/SRTP``
    先前就在这轮扫描里隐形），而立项那条腿又照旧名册指针在 ``projects/`` 里凭空建了同名
    空树（2026-10-16 08:35），两边从此对不上。
    """
    from plobi.mind.paths import real_target
    from plobi.mind.project_tree import TREE_ROOT_RELATIVES

    trees: list[tuple[str, Path]] = []
    for relative in TREE_ROOT_RELATIVES:
        base = real_target(relative, root)
        try:
            children = sorted(base.iterdir(), key=lambda p: p.name)
        except OSError:
            continue
        trees.extend((relative, child) for child in children if child.is_dir())
    return trees


def ensure_mind_project_agents(
    registry: "AgentRegistry | None" = None,
    *,
    mind_root: Path | str | None = None,
) -> "tuple[AgentRegistry, list[str]]":
    """Seed every active Mind project into the L2 registry.

    Walks the category folders (``_mind_project_trees``) one level deep. Skips
    the system-self project (``Mind``), any directory without a dossier profile
    (``dossier.json``; the legacy ``plan.md`` still counts during migration), and
    any profile whose ``registry.state`` matches the skip markers. The remaining
    projects become ``l2_project`` entries with ``category=projects`` (or ``research``
    only when the profile's ``registry.group`` says so). Returns ``(registry, seeded)``
    where ``seeded`` lists the *new* agent names registered on this call.

    Idempotent: re-running the function never duplicates rows. Existing rows
    keep their user-edited ``project_path`` / ``pace`` / ``description`` —
    only blank fields are filled in. ``spawn`` is attempted for each new row
    so the profile + models.json land; failures log and the registry row is
    still kept so the sidebar keeps showing the project.

    投影出来的每一条都盖 ``registered=False``（R-072）：Mind 里有这个项目不等于
    用户给它登记过分身，所以它进 ``mind_candidates`` 而不是 ``roster``。已有行的
    章一个都不改——那是用户的账面。
    """
    from plobi.mind.paths import resolve_root

    reg = registry if registry is not None else load_registry()
    root = resolve_root(mind_root)
    if root is None:
        return reg, []

    seeded: list[str] = []
    for relative, entry in _mind_project_trees(root):
        if entry.name == "Mind":
            continue
        profile_file = entry / "dossier.json"
        legacy_plan = entry / "plan.md"
        if not profile_file.is_file() and not legacy_plan.is_file():
            continue
        front = _read_project_registry(entry)
        if _project_is_skipped(front):
            continue
        safe_id = _project_safe_id(entry.name)
        description = f"{L2_PLACEHOLDER_DESCRIPTION_PREFIX}{entry.name}"
        if safe_id != entry.name:
            description += f" (raw dir name: {entry.name})"
        project_path = _resolve_project_path(entry.name, front)
        category = _project_category(front)

        # R-071: ``Nymo`` 与 ``nymo`` 落到盘上是同一个 profile 家，所以 Mind 这一侧
        # 的名字要先归到已有那条记录的键上，不许因为大小写差异再生出第二条同名分身。
        twin = reg.find_case_twin(safe_id)
        if twin:
            safe_id = twin

        existing = reg.get(safe_id)
        if existing is not None and not existing.archived:
            # Honor user edits: only fill blanks.
            from dataclasses import replace as _replace

            patched = existing
            if not patched.mind_subtree:
                patched = _replace(
                    patched,
                    mind_subtree=f"{relative}/{entry.name}",
                )
            if not patched.project_path and patched.category not in UNBOUND_CATEGORIES:
                patched = _replace(patched, project_path=project_path)
            # 占位句只补在**投影**那一类格子上（``registered=False``，就是这条播种机
            # 自己落下的行）。用户登记过的行留空就是留空：不许把我的样板话写进他的
            # 说明里——名册判据已经不看文本了，身份段也不再看文本，这句一旦写进去
            # 就成了会被当成「这个项目在做什么」念出来的东西。
            if not patched.description and not patched.registered:
                patched = _replace(patched, description=description)
            if patched.category == "butler" and category != "butler":
                patched = _replace(patched, category=category)
            if patched.role != "l2_project":
                patched = _replace(patched, role="l2_project")
            if patched is not existing:
                reg.upsert(patched)
            # WP-L2-DIET / 裁定 33.3: 已有未归档的项目 L2 也要减肥——不能只
            # 靠新 spawn 把清单清掉，旧 profile 留着 terminal 一样会偷开
            # shell。profile 不存在时静默跳过（注册表先行、profile 稍后
            # 落地的过渡期）。
            _diet_existing_l2_project(patched)
            continue

        entry_obj = AgentEntry(
            name=safe_id,
            role="l2_project",
            provider="",
            model="",
            mind_subtree=f"{relative}/{entry.name}",
            skills=(),
            description=description,
            pace=None,
            category=category,
            project_path=project_path,
            archived=False,
            archived_at="",
            source_event_id="",
            # R-072：Mind 投影**不是登记**。这一格就是「谁把这条算成手下」的章：
            # 只有用户/产品在新建那一步盖 ``True``，播种机盖 ``False``。
            registered=False,
        )
        reg.upsert(entry_obj)
        seeded.append(safe_id)
        # Spawn best-effort; failure must NOT delete the registry row
        # (sidebar visibility is the point of the row even when the profile
        # cannot be materialised yet — task: "失败 logger.warning, 注册表行
        # 仍留下").
        spawn_result = None
        try:
            spawn_result = reg.spawn(safe_id)
        except RegistryError as exc:
            logger.warning("plobi: spawn failed for %s: %s", safe_id, exc)
        except Exception as exc:  # spawn may import plobi_cli (not in tests)
            logger.warning("plobi: spawn skipped for %s: %s", safe_id, exc)
        # WP-L2-DIET / 裁定 33.3 原本在这里把 terminal / computer_use / code_execution /
        # session_search / plobi-* 从该 profile config.yaml 剥掉。**B 刀起不再调用**：
        # 家不再被创建，它会去读一个不存在的目录。工具集策略的真源本来就在名册行 /
        # 共享层（不是家里那份出厂 config），收编它属于 C 刀——函数本体仍在
        # `apply_l2_project_diet`，测试也仍钉着它。
    if seeded:
        try:
            reg.save()
        except Exception as exc:
            logger.warning("plobi: registry save skipped: %s", exc)
    return reg, seeded


# WP-PROJECT-PORTFOLIO: plan.md frontmatter / 正文 markers that mean the
# project is no longer progressing. Matches the spelled forms in INDEX
# ("暂停" / "完成" / "废弃") and English common forms. Substring match so
# "已暂停 (TODO 复用)" still hits.
PROJECT_SKIP_STATUS_MARKERS: tuple[str, ...] = (
    "暂停",
    "完成",
    "废弃",
    "paused",
    "completed",
    "abandoned",
    "archived",
    "done",
)

CANCEL_MATCHING_SOURCES: tuple[str, ...] = ("timetable",)

PENDING_STATUSES: tuple[str, ...] = ("pending",)


# ── WP-SEC-VOCAB: read the agenda / decide a pending row (no collection) ────


# ---------------------------------------------------------------------------
# WP-BE-7: aigw 端点解析（quota 腿与 aigw profile 共用这一份）
# ---------------------------------------------------------------------------

AIGW_DEFAULT_BASE = "http://127.0.0.1:8000/v1"

# The only list of env var *names* for this endpoint — the ``model-providers/aigw``
# profile and ``scripts/plobi/doctor.py`` import it instead of writing their own.
AIGW_URL_ENV_VARS: tuple[str, ...] = (
    "PLOBI_QUOTA_HUB_URL",
    "PLOBI_AIGW_URL",
    "PLOBI_QUOTA_AIGW_URL",
)


def aigw_base_url() -> str:
    for env_var in AIGW_URL_ENV_VARS:
        value = (os.environ.get(env_var) or "").strip()
        if value:
            return value.rstrip("/")
    return AIGW_DEFAULT_BASE.rstrip("/")


# Single definition point for the local quota gateway's credential
# (WP-AIGW-CRED-SOURCE / 裁定 81).  Order, and only order:
#   1. an explicit env var, in ``AIGW_KEY_ENV_VARS`` precedence;
#   2. the gateway's own configured key (``aigw/config.yaml`` → ``server.api_key``);
#   3. the development default that ships with the gateway config.
# Anything that needs "which key do we send to aigw" calls :func:`aigw_api_key` —
# the agents layer, the ``model-providers/aigw`` profile (as the api-key
# resolver's declared fallback), ``scripts/plobi/doctor.py`` and
# ``plobi.quota.config``.  None of them re-implements this chain.
AIGW_KEY_ENV_VARS: tuple[str, ...] = (
    "QUOTA_HUB_API_KEY",
    "AIGW_API_KEY",
    "PLOBI_QUOTA_AIGW_KEY",
)
AIGW_DEV_DEFAULT_API_KEY = "sk-local-dev-key"
AIGW_GATEWAY_CONFIG_PATH = Path(__file__).resolve().parents[2] / "quota_hub" / "config.yaml"

# ``api_key: ${AIGW_KEY:-sk-local-dev-key}`` is a valid form in the gateway's own
# config (the gateway expands it at startup), so the reader expands it too —
# otherwise we would send the placeholder text verbatim.
_GATEWAY_KEY_PLACEHOLDER = re.compile(r"^\$\{([A-Z0-9_]+):-([^}]*)\}$")


def gateway_declared_api_key(config_path: Path | None = None) -> str:
    """Return the key the gateway is configured to accept, or ``""``.

    Reads only ``server.api_key`` from the gateway config with no YAML
    dependency (the same no-dep reading ``parse_aigw_port`` in ``doctor.py``
    does).  Missing file, missing key or unreadable file all mean "the gateway
    declares nothing" — the caller decides the fallback.
    """
    path = config_path or AIGW_GATEWAY_CONFIG_PATH
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""

    in_server = False
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if re.match(r"^server:\s*$", line):
            in_server = True
            continue
        if in_server and re.match(r"^\S", line):
            in_server = False
        if in_server:
            match = re.match(r"^\s+api_key:\s*(.+?)\s*$", line)
            if match:
                value = match.group(1).strip("\"'")
                placeholder = _GATEWAY_KEY_PLACEHOLDER.match(value)
                if placeholder:
                    env_name, default = placeholder.group(1), placeholder.group(2)
                    return (os.environ.get(env_name) or "").strip() or default.strip()
                return value
    return ""


def aigw_credential_source() -> str:
    """Label of where :func:`aigw_api_key` would read its value from.

    Observability without the secret: callers log and report this, never the key.
    """
    for env_var in AIGW_KEY_ENV_VARS:
        if (os.environ.get(env_var) or "").strip():
            return f"env:{env_var}"
    if gateway_declared_api_key():
        return "gateway config"
    return "development default"


def aigw_api_key() -> str:
    for env_var in AIGW_KEY_ENV_VARS:
        value = (os.environ.get(env_var) or "").strip()
        if value:
            return value
    return gateway_declared_api_key() or AIGW_DEV_DEFAULT_API_KEY


MASTER_TOOLSET_NAME = "plobi_north_star"

# WP-BE-9 / 裁定 12：L1 中收（可逆）。拿掉 terminal / 跑命令类 / 会话搜索；
# clarify 保留。改 platform_toolsets 列表即可加回，不删插件代码。
# 裁定 49 收回「收工具」：这张名单是**默认不给**，不是**不许有**——用户在产品里
# 勾上的名字活得过中收（``_explicit_toolset_optins`` → ``mid_narrow_toolset_names``
# 的 ``keep=``，见 8e01e9a / ddc4ff8），所以留在这里只会让每次开机多剥一层、
# 用户多勾一次，不会再抹掉他的选择。
# ``memory`` 按裁定 42 / 44 / 48（长期记忆挂在每个人身上）从禁单里拿出来：
# 上一笔 432a3414 的 SOUL 已经写明「该用 ``memory`` 记就记，不用等用户催」，
# 再把它留在禁单里就是 prompt 和 toolset 互相打脸。
L1_DROP_TOOLSETS = frozenset(
    {
        "terminal",
        "session_search",
        "code_execution",
        "computer_use",
    }
)


def _is_full_plobi_composite(name: str) -> bool:
    """True for platform composites like ``plobi-cli`` that embed terminal/search."""
    return name.startswith("plobi-")


# 中收默认名单（对齐 docs/plobi/profiles/master/config.yaml，略宽于「只剩一个」）。
# ``memory`` 既不在这里、也不在禁单里（裁定 49）：默认不铺给 L1，可 profile 一旦
# 携带它（含用户在产品里勾上），``mid_narrow_toolset_names`` 就当 extras 留着。
L1_MID_TOOLSETS: tuple[str, ...] = (
    "web",
    "file",
    "skills",
    "todo",
    "clarify",
    "delegation",
    MASTER_TOOLSET_NAME,
)

# Colon fence — no HTML comments, no "secret" substring (html_comment_injection).
L1_SOUL_BEGIN = ":::PLOBI_L1_ASK_ROUTING:::"
L1_SOUL_END = ":::PLOBI_L1_ASK_END:::"
L1_SOUL_LEGACY_BEGIN = "<!-- PLOBI_L1_SECRETARY_ROUTING -->"
L1_SOUL_LEGACY_END = "<!-- /PLOBI_L1_SECRETARY_ROUTING -->"
# L2 counterpart: same colon-fence style (no HTML comments — the threat scanner
# flags them, see the comment above). Rendered per agent by
# :func:`build_l2_identity_soul_block`, never shared between 分身.
L2_SOUL_BEGIN = ":::PLOBI_L2_IDENTITY:::"
L2_SOUL_END = ":::PLOBI_L2_IDENTITY_END:::"
# 上游出厂就塞在每个 profile ``SOUL.md`` 里的那段样板身份（512 B，无一句人设）。
# 两个品牌变体（``plobi_cli/default_soul.py:4`` 的 "the Plobi team" 与旧装机写下的
# "Nous Research"）句式同一个头，历史模板脚手架另起一行标题——只认**行头**，多写的
# 一个字都算用户自己的话（:func:`_soul_persona_text` 用它把样板剔掉）。
_SOUL_BOILERPLATE_HEADS = (
    "You are Plobi Agent",
    "# Plobi Agent Persona",
)
#: L1 那份 SOUL 合同（两道围栏以内）的唯一真源 = 这个模板文件。改口令就在
#: 这儿改；它不是运行时打补丁——后端启动时 :func:`ensure_l1_secretary_routing_soul`
#: 只把 ``SOUL.md`` 里**围栏以内**那一段整段换成它的内容，围栏以外用户自己写的
#: 人设一个字都不碰（同一个文件里也不许再有第二段 L1 合同，见那道 strip）。
L1_SOUL_TEMPLATE_PATH = (
    Path(__file__).resolve().parents[2]
    / "plugins"
    / "plobi-north-star"
    / "l1_soul_template.md"
)


def load_l1_soul_template() -> str:
    r"""Parse :data:`L1_SOUL_TEMPLATE_PATH` — the one L1 secretary SOUL template.

    Same shape as :func:`load_l1_profile_template`: the template file is the
    source, the runtime ``SOUL.md`` is what it lands into. Fail loud here —
    a template with a missing or reversed fence would make
    :func:`ensure_l1_secretary_routing_soul` either strip user text or stack a
    second contract into the same file, and neither is recoverable from the
    secretary's side.
    """
    text = L1_SOUL_TEMPLATE_PATH.read_text(encoding="utf-8-sig").replace("\r\n", "\n").strip()
    if text.count(L1_SOUL_BEGIN) != 1 or text.count(L1_SOUL_END) != 1:
        raise RegistryError(
            f"{L1_SOUL_TEMPLATE_PATH}: L1 SOUL 模板必须有且只有一对围栏"
            f"（{L1_SOUL_BEGIN} / {L1_SOUL_END}）"
        )
    if text.index(L1_SOUL_BEGIN) > text.index(L1_SOUL_END):
        raise RegistryError(f"{L1_SOUL_TEMPLATE_PATH}: L1 SOUL 模板的两道围栏顺序反了")
    return text + "\n"


#: 载入一次，让读这个常量的那些测试与 :func:`ensure_l1_secretary_routing_soul`
#: 落到盘上的那份字节完全一致。
L1_SOUL_BLOCK = load_l1_soul_template()


def _l1_soul_fence_pairs() -> tuple[tuple[str, str], ...]:
    """Current fence first, then the HTML comment pair that threat-scan blocked."""
    return (
        (L1_SOUL_BEGIN, L1_SOUL_END),
        (L1_SOUL_LEGACY_BEGIN, L1_SOUL_LEGACY_END),
    )


def _l2_soul_fence_pairs() -> tuple[tuple[str, str], ...]:
    """The L2 identity fence (colon style, same reason as L1's)."""
    return ((L2_SOUL_BEGIN, L2_SOUL_END),)


def _strip_soul_spans(text: str, pairs: tuple[tuple[str, str], ...]) -> str:
    """Remove every fenced span named in ``pairs`` (colon markers or comments)."""
    remaining = text
    changed = True
    while changed:
        changed = False
        for begin, end in pairs:
            if begin not in remaining or end not in remaining:
                continue
            start = remaining.index(begin)
            try:
                stop = remaining.index(end, start) + len(end)
            except ValueError:
                continue
            remaining = remaining[:start] + remaining[stop:]
            changed = True
            break
    return remaining


def _strip_l1_soul_spans(text: str) -> str:
    """Remove every known L1 routing fence (old HTML or current colon markers)."""
    return _strip_soul_spans(text, _l1_soul_fence_pairs())


def _strip_l2_soul_spans(text: str) -> str:
    """Remove every L2 identity fence from ``text``."""
    return _strip_soul_spans(text, _l2_soul_fence_pairs())


def mid_narrow_toolset_names(names: list[str] | None, keep=()) -> list[str]:
    """Rewrite a toolset name list for L1 mid-narrow (reversible).

    ``keep`` is the same explicit user opt-in escape hatch as
    :func:`_filter_l2_project_toolsets` (both passes share the discriminator in
    :func:`_explicit_toolset_optins`): drop-set names in it survive the narrow,
    but only ``L1_DROP_TOOLSETS`` ones — a ``plobi-*`` composite is never an
    opt-in, because ``_save_platform_tools`` refuses to write platform default
    toolsets (see :func:`apply_l1_mid_toolsets` for the baseline).
    """
    raw = [str(n) for n in (names or []) if str(n).strip()]
    keep_names = set(keep)
    if not raw or any(_is_full_plobi_composite(n) for n in raw) or any(
        n in L1_DROP_TOOLSETS for n in raw
    ):
        extras = [
            n
            for n in raw
            if not _is_full_plobi_composite(n)
            and n not in L1_DROP_TOOLSETS
            and n not in L1_MID_TOOLSETS
        ]
        # ``kept`` is only ever non-empty on this branch: an opt-in name is by
        # definition a drop-set name, which is what sent us here. The tail
        # ``extras + kept`` is a fixed point, so re-running stays unchanged.
        kept = [n for n in raw if n in keep_names and n in L1_DROP_TOOLSETS]
        return list(L1_MID_TOOLSETS) + extras + kept
    result = [n for n in raw if n not in L1_DROP_TOOLSETS]
    for required in (MASTER_TOOLSET_NAME, "clarify"):
        if required not in result:
            result.append(required)
    return result


def apply_l1_mid_toolsets(config: dict) -> bool:
    """Mutate active-profile config: mid-narrow cli/gateway + top-level toolsets.

    Runs at every plugin load via :func:`ensure_north_star_toolset`, so it is
    the pass that actually reverted the 主秘书 / 日程 toggles. It now honours
    explicit user opt-ins exactly like :func:`apply_l2_project_diet` does —
    same discriminator (:func:`_explicit_toolset_optins`, reused as-is), so
    ``terminal`` switched on in ``plobi tools`` / the desktop Toolsets panel
    after a narrowing survives the next start instead of being silently
    stripped. The policy itself is unchanged: names the profile inherited in its
    top-level ``toolsets`` baseline are still narrowed everywhere.

    Baseline caveat, because this pass reads ``load_config()`` (merged with
    ``DEFAULT_CONFIG``) rather than the raw profile file like the diet does: a
    profile that never authored a top-level ``toolsets`` inherits
    ``DEFAULT_CONFIG``'s ``["plobi-cli"]`` composite. A composite enumerates
    nothing, so absence from it proves nothing and no opt-in can be attributed —
    in that case (empty or composite baseline) there is no trustworthy baseline
    and the pass narrows exactly as it did before, so the first-ever pass on a
    fresh install and on every ``plobi-cli`` profile is bit-for-bit today's
    behaviour. Once this pass has written a real top-level list, later toggles
    are recognised. Idempotent: nothing to change returns False without writing.
    """
    changed = False
    # Read the baseline BEFORE the top-level narrow below, otherwise the first
    # pass would mistake the very name it is about to remove for an opt-in.
    baseline = [str(n) for n in (config.get("toolsets") or []) if str(n).strip()]
    has_baseline = bool(baseline) and not any(
        _is_full_plobi_composite(n) for n in baseline
    )
    narrowed = mid_narrow_toolset_names(list(config.get("toolsets") or []))
    if list(config.get("toolsets") or []) != narrowed:
        config["toolsets"] = narrowed
        changed = True

    platforms = config.get("platform_toolsets")
    if not isinstance(platforms, dict):
        platforms = {}
        config["platform_toolsets"] = platforms
        changed = True
    for platform in ("cli", "gateway"):
        current = platforms.get(platform)
        as_list = list(current) if isinstance(current, list) else []
        keep = (
            _explicit_toolset_optins([str(n) for n in as_list], baseline)
            if has_baseline
            else ()
        )
        # Missing platform list falls back to plobi-cli at runtime — write mid.
        next_list = mid_narrow_toolset_names(as_list if as_list else ["plobi-cli"], keep=keep)
        if as_list != next_list:
            platforms[platform] = next_list
            changed = True
    return changed


# WP-L2-DIET / 裁定 33.3：项目 L2（不是 L2-agenda）必须没有 terminal /
# 跑命令 / 截屏 / 会话搜索，也不带 plobi-* 复合工具集。L1 的中收逻辑把
# ``plobi-cli`` / ``terminal`` / ``session_search`` 都视为污染源；这里
# 复用同一份口径，删掉得彻底——留 terminal 是把 L2 重新变回工人。
L2_PROJECT_DROP_TOOLSETS = frozenset(
    {
        "terminal",
        "computer_use",
        "code_execution",
        "session_search",
    }
)


def _filter_l2_project_toolsets(names, keep=()) -> list[str]:
    """Drop the diet list + every ``plobi-*`` composite from a name list.

    Preserves any other name (file / web / skills / todo / clarify / …) in
    the original order so config diffs stay minimal. Returns a new list.

    ``keep`` names an *explicit user opt-in* (see :func:`apply_l2_project_diet`):
    drop-set names in it survive, but only the diet-set ones — a ``plobi-*``
    composite is never an opt-in, because the product's toggle refuses to write
    platform default toolsets and 裁定 33.3 calls composites out separately.
    """
    keep_names = set(keep)
    return [
        n
        for n in names
        if not _is_full_plobi_composite(n)
        and (n not in L2_PROJECT_DROP_TOOLSETS or n in keep_names)
    ]


def _explicit_toolset_optins(platform_names, baseline_names) -> set:
    """Names the user opted into through the product's own toggle.

    Shared discriminator for BOTH narrowing passes — :func:`apply_l2_project_diet`
    and :func:`apply_l1_mid_toolsets` — so there is only one rule about who
    wrote what. Their own output, and every policy pass that seeds a profile's
    ``config.yaml``, writes the top-level ``toolsets`` together with the platform
    lists (:func:`apply_l1_mid_toolsets` does exactly that triple write, and a
    profile created with ``create_profile(clone_config=True)`` arrives holding
    all three from its clone source).
    So a drop-set name that shows up in ONE platform list while absent from the
    profile's top-level ``toolsets`` baseline cannot have come from any of those
    passes: the only writer with that shape is ``_save_platform_tools``
    (``plobi tools`` / the desktop Toolsets panel), which writes a single named
    platform and never the top level. Treat those as the user's explicit choice,
    so a re-run cannot silently revert it.

    ``baseline_names`` must be an *authored enumeration* — the caller decides
    what counts (the diet reads the raw profile file; the L1 pass refuses to use
    ``DEFAULT_CONFIG``'s composite fallback, which proves nothing).
    """
    return set(platform_names) - set(baseline_names)


def apply_l2_project_diet(profile_dir: Path | str | None) -> bool:
    """**清理工具**（2026-10-24 C 刀起，不再是真源）：strip terminal /
    computer_use / code_execution / session_search + every ``plobi-*``
    composite from a project L2 profile's ``config.yaml``。

    ⚠️ 真源已搬进**名册行的 ``disabled_toolsets``**（:func:`diet_entry_toolsets`，
    读侧经 :func:`profile_config_overlay` 叠 ``agent.disabled_toolsets``）。本函数
    只剩一个用途：把**已经存在的 24 个分身家**里那份历史 config 剥干净——那些家
    终将被 quarantine 搬走，搬之前让它们别带着全套工具集的复印件。新行不要再调它。

    Touches both the top-level ``toolsets`` and the per-platform
    ``platform_toolsets`` (cli + gateway). Idempotent and reversible: a tool
    the user (re-)enabled **after** the diet ran is left alone, so the toggle
    in ``plobi tools`` / the desktop Toolsets panel actually sticks. See
    :func:`_explicit_toolset_optins` for how a later opt-in is recognised without
    keeping a second copy of the diet's removals — a name present in a
    platform list but missing from that profile's top-level ``toolsets``
    baseline was written by the toggle, not by a policy pass, so re-running
    the diet on an untouched profile strips exactly what it strips today.
    Editing ``toolsets`` at the top level is *not* how you add a tool back
    (the baseline is the profile's own worker bundle, and the diet polices
    it); use ``plobi tools``.

    Returns True if the file was rewritten, False if no changes were needed
    (or the profile directory has no readable config — silent no-op so the
    caller can fire-and-forget during ``ensure_mind_project_agents``).
    """
    import yaml

    if profile_dir is None:
        return False
    cfg_path = Path(profile_dir) / "config.yaml"
    if not cfg_path.is_file():
        return False
    try:
        cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return False
    if not isinstance(cfg, dict):
        return False

    changed = False

    top = cfg.get("toolsets")
    # The baseline the toggle never writes: whatever this profile's top-level
    # toolsets declares *as authored* is what it inherited, so anything else
    # in a platform list can only be the user's explicit opt-in. Read it
    # BEFORE the strip below, otherwise the first pass would mistake the very
    # name it is about to remove for an opt-in and leave it in place.
    baseline = [str(n) for n in top] if isinstance(top, list) else []
    if isinstance(top, list):
        new_top = _filter_l2_project_toolsets(top)
        if new_top != top:
            cfg["toolsets"] = new_top
            changed = True

    platforms = cfg.get("platform_toolsets")
    if isinstance(platforms, dict):
        for platform in ("cli", "gateway"):
            listed = platforms.get(platform)
            if isinstance(listed, list):
                new_listed = _filter_l2_project_toolsets(
                    listed,
                    keep=_explicit_toolset_optins([str(n) for n in listed], baseline),
                )
                if new_listed != listed:
                    platforms[platform] = new_listed
                    changed = True

    if not changed:
        return False
    cfg_path.write_text(
        yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    return True


def shared_skills_root() -> Path:
    """The one skill root every L2 分身 reads (裁定 42 §42.2 共享层).

    Anchored to the **default** profile home, not to the active ``PLOBI_HOME``:
    ``spawn`` may itself run inside a profile, and a 分身 must not inherit
    another 分身's copy as the shared root.
    """
    from plobi_cli.agent_identity import get_profile_dir

    return get_profile_dir("default") / "skills"


def apply_shared_skills_root() -> bool:
    """Point the one ``config.yaml`` at :func:`shared_skills_root`（顶层那一份，只写一次）。

    裁定 42 §42.3 第 2 条：分身不再自带技能，可见性来自 ``skills.external_dirs``
    （``agent/skill_utils.get_external_skills_dirs`` 读取；``tools/skills_tool.py`` 与
    ``agent/prompt_builder.py`` 都按「本地主根优先、同名外部技能跳过」合并，所以列表不会翻倍）。

    以前这一句写进**每个分身家**的 ``config.yaml``：现核 24 份、那一条 24 份逐字相同
    ——它从来不是"这个分身特有的东西"，只是复印件。裁 166 把配置收成一份之后就写那一份：
    对顶层家来说那个路径正是它自己的本地主根（``get_external_skills_dirs`` 会跳过同名项，
    等于 no-op），对分身来说它是真正的外部根——同一个值，两边都对，而且只存了一处。

    Idempotent and preserves every other key:
    - shared root missing (fresh install, nothing seeded) → still written; the
      scanner skips non-existent dirs, and the binding self-heals once the root exists.
    - the root is already listed (compared after ``expanduser`` + ``absolute``)
      → no-op, so repeated ``spawn`` never appends a second entry.
    - any external dir the user listed themselves is preserved; we only append
      ours, never reorder or drop.
    """
    import yaml
    from plobi_constants import get_shared_plobi_root

    root = str(shared_skills_root().expanduser().absolute())
    cfg_path = get_shared_plobi_root() / "config.yaml"

    if not cfg_path.is_file():
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        cfg_path.write_text(
            yaml.safe_dump(
                {"skills": {"external_dirs": [root]}},
                sort_keys=False,
                allow_unicode=True,
            ),
            encoding="utf-8",
        )
        return True

    try:
        cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return False
    if not isinstance(cfg, dict):
        return False

    skills = cfg.get("skills")
    if not isinstance(skills, dict):
        skills = {}
    external = skills.get("external_dirs")
    if isinstance(external, str):
        external = [external]
    if not isinstance(external, list):
        external = []

    def _same_path(entry: object) -> bool:
        try:
            return Path(os.path.expanduser(str(entry))).absolute() == Path(root)
        except (OSError, RuntimeError):
            return False

    # Idempotent: the shared root is already there → nothing to do.
    if any(_same_path(entry) for entry in external):
        return False

    skills["external_dirs"] = [*external, root]
    cfg["skills"] = skills
    cfg_path.write_text(
        yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    return True
    return True


def diet_entry_toolsets(entry: "AgentEntry") -> bool:
    """WP-L2-DIET 的**真源**：把 l2_project 的工具集减肥写进名册那一行。

    2026-10-24 C 刀。旧形状是 :func:`apply_l2_project_diet` 剥分身家
    ``config.yaml``——B 刀（WP-REGISTRY-NO-HOME）之后家不再被建出来，那条路
    两条触发路径全 no-op；而且它剥的从来是 ``create_profile`` 给的**出厂默认**，
    跟一个默认值较劲。真源按裁定 42 §42.2 / 裁 166 落在名册行：这一行的
    ``disabled_toolsets`` 就是"这个分身不该有什么工具"，读侧由
    :func:`profile_config_overlay` 叠进 ``agent.disabled_toolsets``
    （``plobi_cli/tools_config.py`` 的既有消费点），不落第二份盘。

    写什么：``L2_PROJECT_DROP_TOOLSETS`` 那四个（terminal / computer_use /
    code_execution / session_search——"留 terminal 是把 L2 重新变回工人"）。
    **不写** ``plobi-*`` 复合：它不是用户勾来的，是平台复合，禁它连带禁掉
    里面every healthy leaf；复合的处置归 ``_filter_l2_project_toolsets``
    （生成 / 清理侧）。

    契约：
    - 幂等——已写过的名字不重复追加；
    - 只追加——用户自己声明过的黑名单一字不动；
    - 可逆——用户在桌面 / ``plobi tools`` 把某一项开回来 = 改名册行
      （``web_server.py`` 的 ``disabled_toolsets`` 开关），这里不会在读时
      重新剥掉（读时过滤会破坏 diet 的可逆契约，所以不做读时过滤）；
    - 非 l2_project / 已归档 / 类别不在 projects·research ⇒ 不写。
    """
    if entry.role != "l2_project" or entry.archived:
        return False
    if entry.category not in ("projects", "research"):
        return False
    banned = sorted(L2_PROJECT_DROP_TOOLSETS)
    have = {str(n).casefold() for n in entry.disabled_toolsets}
    missing = [n for n in banned if n.casefold() not in have]
    if not missing:
        return False
    merged = tuple(entry.disabled_toolsets) + tuple(missing)
    # AgentEntry 是 frozen dataclass。object.__setattr__ 是 dataclasses 自己的
    # 逃生口（pickle / copy 重建同款），运行中就地改一格用它是这个类的既定做法；
    # 换成 replace() 会返回新对象，而三个调用方（spawn / ensure_mind_project_agents
    # / _diet_existing_l2_project）都是"改进去后 reg.save()"的形状，接不住。
    object.__setattr__(entry, "disabled_toolsets", merged)
    return True


def _diet_existing_l2_project(entry: "AgentEntry") -> None:
    """WP-L2-DIET / 裁定 33.3: 对注册表里已存在的未归档 ``l2_project`` 行
    也走减肥。

    2026-10-24 C 刀：落点从「剥家里的 config.yaml」换成
    :func:`diet_entry_toolsets`（写名册行）。家不在盘上也照写——真源本来就是
    名册行，盘上那份只是历史复印件。旧的家清理仍由
    :func:`apply_l2_project_diet` 承担（定位已降级为清理工具）。
    """
    if entry.role != "l2_project":
        return
    if entry.archived:
        return
    if entry.category not in ("projects", "research"):
        return
    try:
        diet_entry_toolsets(entry)
    except Exception as exc:
        logger.debug("plobi: l2 diet (registry row) skipped for %s: %s", entry.name, exc)


def ensure_l2_agenda_toolsets(profile_dir: Path | str) -> bool:
    """Keep terminal available on agenda L2 profiles (do not inherit L1 mid-narrow).

    Reversible: only appends ``terminal`` when neither ``plobi-cli`` nor
    ``terminal`` is listed. Never rewrites L1/default.
    """
    import yaml

    cfg_path = Path(profile_dir) / "config.yaml"
    if not cfg_path.is_file():
        return False
    try:
        cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return False
    if not isinstance(cfg, dict):
        return False

    changed = False
    platforms = cfg.get("platform_toolsets")
    if not isinstance(platforms, dict):
        platforms = {}
        cfg["platform_toolsets"] = platforms
        changed = True
    for platform in ("cli", "gateway"):
        listed = platforms.get(platform)
        if not isinstance(listed, list):
            listed = []
            platforms[platform] = listed
            changed = True
        names = [str(n) for n in listed]
        if "plobi-cli" in names or "terminal" in names:
            continue
        listed.append("terminal")
        changed = True

    top = cfg.get("toolsets")
    if isinstance(top, list):
        names = [str(n) for n in top]
        if "plobi-cli" not in names and "terminal" not in names:
            top.append("terminal")
            changed = True

    if not changed:
        return False
    cfg_path.write_text(
        yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    return True


def ensure_l1_secretary_routing_soul(*, home: Path | str | None = None) -> bool:
    """Upsert soft-routing instructions into the active profile ``SOUL.md``.

    L1 and L2 contracts are mutually exclusive: writing the L1 block also drops
    any L2 identity fence in the same file (see :func:`ensure_l2_identity_soul`).
    """
    from plobi_constants import get_plobi_home

    soul_path = Path(home) / "SOUL.md" if home is not None else get_plobi_home() / "SOUL.md"
    if not soul_path.parent.is_dir():
        # 家目录都还没落地就没有 L1 可写：为了一份 SOUL.md 把 profile 建出来是
        # 第二真源（同 :func:`ensure_l2_identity_soul` 的判据），交给播种方跳过。
        return False
    existing = ""
    if soul_path.is_file():
        existing = soul_path.read_text(encoding="utf-8")
    stripped = _strip_l1_soul_spans(_strip_l2_soul_spans(existing)).rstrip()
    updated = (stripped + "\n\n" if stripped else "") + L1_SOUL_BLOCK.strip() + "\n"
    if updated == existing:
        return False
    soul_path.write_text(updated, encoding="utf-8")
    return True


def strip_l1_secretary_routing_soul(*, home: Path | str | None = None) -> bool:
    """Drop the L1 secretary contract from a **non-default** profile's SOUL.md.

    ``--clone`` copies the root ``SOUL.md`` whole, L1 fence included, and the
    root file is an **L1-only** contract — its first line declares 「你是本机
    Plobi 总秘书（L1）」. A 分身 carrying it claims to be the secretary (the
    ``profiles/l2-agenda`` clone; ``Docs/ARCH-RULINGS_2026-09-08.md`` §6(a)).
    Whatever identity survives the strip is left alone. Idempotent.
    """
    from plobi_constants import get_plobi_home

    soul_path = Path(home) / "SOUL.md" if home is not None else get_plobi_home() / "SOUL.md"
    if not soul_path.is_file():
        return False
    existing = soul_path.read_text(encoding="utf-8")
    stripped = _strip_l1_soul_spans(existing)
    if stripped == existing:
        return False
    stripped = stripped.strip()
    soul_path.write_text(stripped + "\n" if stripped else "", encoding="utf-8")
    return True


def _soul_persona_text(text: str) -> str:
    """Return the part of ``SOUL.md`` that counts as this 分身's own character.

    Both generated fences and the upstream boilerplate come out; whatever is
    left was written by a person — 裁定 42 §42.2 第 6 项 reserves 人设片段 as one
    of the only three per-project overrides (实测 14 个分身目前都还没有).
    ``_SOUL_BOILERPLATE_HEADS`` is the 512-byte upstream stub shipped into every
    profile, so a plain "anything outside the fence" test would mistake it for a
    persona.
    """
    body = _strip_l1_soul_spans(_strip_l2_soul_spans(text))
    try:
        # 旧装机种过一版纯注释脚手架（同样是零人设）。判据复用底座那一份，
        # 不在这里再抄一遍模板。
        from plobi_cli.default_soul import is_legacy_template_soul

        if is_legacy_template_soul(body):
            return ""
    except ImportError:  # pragma: no cover - plobi_cli 在部分测试环境不可解析
        logger.debug("plobi: legacy SOUL template check skipped", exc_info=True)
    kept = [
        line
        for line in body.splitlines()
        if not line.strip().startswith(_SOUL_BOILERPLATE_HEADS)
    ]
    return "\n".join(kept).strip()


def build_l2_identity_soul_block(
    entry: AgentEntry, *, persona_written: bool = False
) -> str:
    """Render one 分身's **assignment** from fields the registry already has.

    Only ``AgentEntry`` columns (name / display_name / description /
    project_path / mind_subtree) — no new schema, no config keys. Two rulings
    shape the text:

    * 裁定 45 — 内部字段值不进提示词。``id`` / ``role`` / ``l2_project`` 这类词
      模型会原样念给用户听（实测已经发作过），用户听不懂也不该听见；
      L1 / L2 是他自己的说法，留着。
    * 这一段是**分工**，不是整个人。围栏之外那份人设才是这个人说话的样子，
      生成器一个字都不碰它；``persona_written`` 只决定最后一条怎么说。
    """
    who = entry.display_name or entry.name
    # ``name`` 是 slug、``display_name`` 是人名（裁定 37.1）；项目自己的名字落在
    # Mind 那棵子树的末段上。
    project = (entry.mind_subtree or "").rstrip("/").rsplit("/", 1)[-1].strip() or who
    lines = [
        L2_SOUL_BEGIN,
        "## 你负责的这一摊（WP-L2-IDENTITY，硬规则，不可绕过）",
        "",
        f"- 你是 **{who}**。用户这么叫你，你就这么自称；这个名字之外没有别的身份等着你认领。",
    ]
    if entry.project_path:
        # 裁定 42.1：注册表里那 12 条 ``project_path`` 还是报废 Windows 机的路径。
        # 所以这句只说「登记的是哪里」，打不打得开交给分身自己判——它得问，不能跑到
        # 别的项目目录里去干活。
        lines.append(
            f"- 你手上只有 **{project}** 这一个项目，它登记的工作目录是 `{entry.project_path}`：代码、文件、命令都在这个地方发生，别的目录不归你动。这个目录打不开，就是路径还没对本平台改过来——问用户一句，不要去别的项目里干活。"
        )
    else:
        lines.append(
            f"- 你手上只有 **{project}** 这一个项目，但它的工作目录还没定下来。要动文件先问用户一句，不要替他猜一个路径。"
        )
    if entry.mind_subtree:
        from plobi.mind.paths import normalize_logical

        lines.append(
            f"- 你在 Mind 知识库里的地方是 `{normalize_logical(entry.mind_subtree)}`：这个项目的计划、进度、结论写在这棵树里，别的树只读不改。"
        )
    # 用途那一格只印**人写过的话**：``registered`` 那一格判「这行算不算他登记的手下」，
    # :func:`_is_placeholder_project_description` 判「这句话是不是播种机自己写的备注」——
    # 那句备注印在这里，分身就会把 ``l2_project for Mind project …`` 当自己的用途念出来
    # （裁定 45：内部字段值不进提示词）。识别桩只在这一处用，不参与任何判据。
    if (
        entry.registered
        and entry.description
        and not _is_placeholder_project_description(entry.description)
    ):
        lines.append(f"- 这个项目在做的事：{entry.description}")
    else:
        lines.append(
            "- 这个项目到底在做什么，目前没有人写下来。别拿任何一句现成的模板话当用途：先读你自己那棵树，读不出来就问用户一句，他说了你记下来。"
        )
    # 用户 10-31：「L1 和 L2 干活第一反应都不是统筹，而是自己干活」。北星 §4 的原话
    # 是「L2 是统筹者，不是独力干将」——这一句以前只在文档里，围栏里只有「你手上只有
    # 这一个项目」和「统筹归 L1」，于是一个项目 Agent 读到的岗位是干活的。
    lines.append(
        "- **你是这一摊的统筹者，不是独力干将**：拿到一件事先拆开，能派出去的就派——"
        "整理文件、查网页、操作软件/电脑这类活优先交给即用即丢的专家（`delegate_task`"
        " 的 expert 填 file / browser / software / computer）；你手上若还给了外包口"
        "（把活交给本机别的 AI 软件），也优先外包。你自己做的是判断、拆解、验收、"
        "把结果收成一句人话。工具没给或专家起不来，就如实说一句「这个我现在做不了」，"
        "再退回自己扛——不要一声不响全自己干。"
    )
    # 11-02：上一版只写了「能派的就派」——那是一句品德，不是一个判据，模型照样
    # 自己上手。用户这一轮点名要的是**界线**，所以这里给一条它每件事都能自问的：
    # 要不要动文件/跑命令/开浏览器。另外把「派完这一轮就交不出东西」这个真实的
    # 心理诱因堵掉（delegate_task 是后台的，模型会因此觉得自己没交付）。
    lines.append(
        "- 界线只有一条：**这件事要不要动文件、跑命令、开浏览器**。要动，就派出去；"
        "不动的那些——判断、拆解、验收、跟用户把话说清楚——才是你自己的活。"
        "「派完这一轮就交不出成品」不是自己动手的理由：派完先回一句「已经派下去了，"
        "结果回来我接着看」，这就是交付。"
    )
    # 11-04：派工时必须说清「这是哪一类专家」。用户点名的口径是两条腿分开——
    # 带 expert 的那几趟进左栏「专家」抽屉、点得开日志；不带的算普通子智能体，
    # 只进底栏那一格。不带 expert 就等于把专家降级成普通子智能体，用户看不见它。
    lines.append(
        "- 派工时 `expert` 不是可选项：动文件填 `file`、查网页填 `browser`、"
        "装/跑/修软件填 `software`、系统与窗口操作填 `computer`。只有**不属于这四类**的"
        "杂活才不带 expert——那种是普通子智能体，日志进底栏那一格，不会出现在「专家」抽屉里。"
    )
    # 外部会话：规则只能按目录与标题判，判不准的会停在「未判」。让 L2 主动收口，
    # 别让用户自己去一条条点（用户 11-04：目录相同肯定是有关的，剩下交给 L2 判）。
    lines.append(
        "- 本机别的 AI 软件里的会话，扫描时每条都带一个判断：**工作目录或标题点到你的直接算有关**，"
        "剩下的停在「未判」。未判的别放着——用 `external_sessions_scan` 扫一遍，读标题与项目判一次，"
        "再用 `external_sessions_mark` 记下来；判不准就留空，不要猜。"
    )
    lines += [
        "- 你的长期记忆是**本机唯一的那一份共享账本**：别的 Agent 也往同一本里写，每条带「哪个项目」的标签。账本只记发生过什么——**「我是谁」不看账本，看这份文件**。",
        "- 你带着这个项目自己那条「现在」（裁定 44：同一时刻只有一条现在）。用户问「你在忙什么 / 上次推到哪了」，你要答得上来；答不上就说不记得，不要拿别的项目的进度顶。",
        "- **职责不是从共享记忆里挑一个来当**。账本里写着别人的分工——比如总秘书那套「架构 + 验证 + 文档 + 测试 + 小修」——那是他的岗位，不是你的。上面没写你的职责，就照项目归属说话；说不准就问用户一句，不要自己认领一个头衔。",
        "- 你**不是**本机 Plobi 总秘书（L1），**不是** 通道模型的名字、**不是** 通用 chatbot、**不是** Cursor / Claude Code 这种 GUI 工具里钻的工具人。跨项目的派工、日程入口、跨项目统筹归 L1（裁定 44：一嘴一个 Agent）——**本项目内部的统筹是你自己的**；不要转述调度器的角色，不要替别的项目作答，也不要自称总秘书。",
    ]
    if persona_written:
        lines.append(
            "- 上面这些是你的**分工**，不是你这个人。这份文件围栏之外还写着你说话的样子、你的脾气——那才算你自己，照它说，别改它。"
        )
    else:
        lines.append(
            "- 上面这些是你的**分工**，不是你这个人。你说话的样子、你的脾气写在这份文件围栏之外；那里还空着，用户想让你更像个人的时候就补在那里。"
        )
    lines.append(L2_SOUL_END)
    return "\n".join(lines) + "\n"


# ── 身份的共享层落点（WP-AGENT-SOUL-SHARED）────────────────────────────────
#
# 裁定 42 §42.2 第 6 项：一个项目分身 = 名册一行 + 最多三个覆盖（人设片段 /
# 模型 / 密钥）。今天人设片段的承载仍是「一整个家」里的 ``SOUL.md``——
# 这是 24 个家清不掉的最后一处承重，也是 10-23 11:36 那次「搬完家左栏全瘫」
# 的同类风险（那次瘫的是判据，这次会是身份）。
#
# 落点换成共享层下一份按 agent 区分的文件，与
# :func:`plobi.routing.models.shared_config_path` 同一个 root、同一套理由。


def shared_souls_root() -> Path:
    """身份那一片的根：共享层下 ``souls/``（一份，不按分身复印）。"""
    from plobi_constants import get_shared_plobi_root

    return get_shared_plobi_root() / "souls"


def shared_soul_path_for(agent_name: str) -> Path:
    """某个 Agent 的身份文件。键 = **名册 name**，不是 profile 名。

    读侧（``agent/prompt_builder.load_soul_md``）手里只有 ``PLOBI_AGENT_ID``
    ——那是 ``build_child_command`` 放进去的 ``entry.name``。两侧用同一个键，
    读侧就不必反查名册，也不会因为「name 与 profile 名不一致」而读空。
    """
    return shared_souls_root() / f"{_project_safe_id(agent_name)}.md"


def shared_soul_path(entry) -> Path:
    """这一行的身份文件（键是它的名册 name）。"""
    return shared_soul_path_for(entry.name)


def identity_name_for_key(key: str | None) -> str:
    """把身份的**任意写法**翻回名册那一行的 ``name``；翻不到回空串。

    运行时身份有好几种写法：名册 name（真源）、它的小写、记忆那颗 slug
    （``tools.memory_tool.resolve_agent_slug`` 会把「（）」这类字符换成 '-'）、
    profile 家名（``l2-course-*``），以及 ``souls/`` 里那个身份文件名。读侧
    （``agent/prompt_builder.load_soul_md``）以前只拿 slug 去算文件名——名字一
    变形就算不出自己那份，于是静默回落到顶层那份 **L1 秘书 SOUL**，分身当场
    自称总秘书（10-31 现读：名册 24 行里有 5 行中招，全是名字带全角括号或
    罗马数字的课程行）。

    判据：**认得出就翻回那一行的 name**，认不出就回空串（调用方据此不给身份，
    而不是去拿 L1 那份）。
    """
    raw = (key or "").strip()
    if not raw:
        return ""
    try:
        registry = load_registry()
    except Exception as exc:  # noqa: BLE001 - 名册读不到就当认不出
        logger.debug("plobi: identity lookup skipped (registry): %s", exc)
        return ""
    try:
        from tools.memory_tool import resolve_agent_slug
    except Exception:  # noqa: BLE001 - 记忆那条腿不可用就少一种写法
        resolve_agent_slug = None
    for entry in registry.agents.values():
        if getattr(entry, "archived", False):
            continue
        candidates = {entry.name, entry.name.lower()}
        # profile 名这一格已随概念一起删掉（2026-11-06）：身份名只剩「名册 name 派生的
        # slug」这一种来历，直接派生同一个值放进来，别再去问一个不存在的属性。
        try:
            derived = derive_runtime_slug(entry.name)
        except Exception:  # noqa: BLE001 - 派生不出来就少一种写法
            derived = ""
        if derived:
            candidates.add(derived)
            candidates.add(derived.lower())
        try:
            candidates.add(shared_soul_path_for(entry.name).stem)
        except Exception:  # noqa: BLE001 - 算不出文件名就少一种写法
            pass
        if resolve_agent_slug is not None:
            try:
                candidates.add(resolve_agent_slug(entry.name))
            except Exception:  # noqa: BLE001
                pass
        if raw in candidates:
            return entry.name
    return ""


def ensure_l2_identity_soul(
    entry: AgentEntry, *, home: Path | str | None = None
) -> bool:
    """Upsert a project 分身's OWN assignment into its identity file.

    **落点（WP-AGENT-SOUL-SHARED，2026-10-23）**：默认写**共享层**那一份
    :func:`shared_soul_path`（``<shared root>/souls/<名册 name>.md``），
    ``agent/prompt_builder.load_soul_md()`` 在 ``PLOBI_AGENT_ID`` 存在时优先读它。
    身份从此不由「一整个家」承载——那是裁定 42 要拆掉的东西，也是 10-23 11:36
    那次事故的同类风险。显式传 ``home=`` 时仍写那个家的 ``SOUL.md``（旧形状，
    给还在按家起的调用方一条退路）。

    其余契约不变：Shape copied from
    :func:`ensure_l1_secretary_routing_soul`: strip the known fences, then
    append; the two contracts never coexist in one file. No-op when the content
    already matches, and never written onto the default (L1) profile.

    裁定 42 §42.2 第 6 项 reserves a per-project 人设片段, and that fragment lives
    in this same file **outside** the fence. Only this function's own fence is
    stripped, so hand-written character text survives regeneration; whether such
    text exists is decided by :func:`_soul_persona_text` (the upstream stub is
    not a persona) and handed to the renderer as ``persona_written``.
    """
    if entry.role == L1_SECRETARY_ROLE or entry.profile_name == "default":
        return False

    if home is not None:
        # 旧形状（显式指定一个家）：家不在就按旧契约 no-op——那是「分身还没落地」，
        # 不为一份 SOUL.md 凭空造 profile 目录（宁可不给，也不编造落点）。
        target = Path(home)
        if not target.is_dir():
            return False
        soul_path = target / "SOUL.md"
    else:
        # 新形状（默认，WP-AGENT-SOUL-SHARED）：身份落在共享层下按 agent 一份，
        # **家不再是前提**。判据曾被「盘上有没有家」绑住过一次，一次数据搬家
        # 就把整条左栏打瘫（10-23 11:36 现读 0/0/24）；身份不再犯同一个错。
        soul_path = shared_soul_path(entry)
        try:
            soul_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.warning(
                "plobi: shared soul dir not writable for %s: %s", entry.name, exc
            )
            return False
    existing = ""
    if soul_path.is_file():
        existing = soul_path.read_text(encoding="utf-8")
    stripped = _strip_l1_soul_spans(_strip_l2_soul_spans(existing)).rstrip()
    block = build_l2_identity_soul_block(
        entry, persona_written=bool(_soul_persona_text(existing))
    ).strip()
    updated = (stripped + "\n\n" if stripped else "") + block + "\n"
    if updated == existing:
        return False
    soul_path.parent.mkdir(parents=True, exist_ok=True)
    soul_path.write_text(updated, encoding="utf-8")
    return True


# ---------------------------------------------------------------------------
# WP-L1-SECRETARY-FORM — one definition of "L1 秘书形态", applied from two doors
# ---------------------------------------------------------------------------

#: The only L1 default-profile template in the repo. Its own comment says the
#: runtime config.yaml is not the deliverable — the point of reading it here is
#: that *nothing used to*: the file existed, the budget hook only touched a live
#: agent, and the plugin that would have persisted these knobs never loaded
#: because the plugin itself was the thing missing.
L1_PROFILE_TEMPLATE_PATH = (
    Path(__file__).resolve().parents[2] / "plugins" / "plobi-north-star" / "l1_profile_template.yaml"
)


def load_l1_profile_template() -> dict:
    """Parse :data:`L1_PROFILE_TEMPLATE_PATH` (the template is the single source)."""
    import yaml

    return yaml.safe_load(L1_PROFILE_TEMPLATE_PATH.read_text(encoding="utf-8")) or {}


def _merge_l1_profile_template(config: dict) -> bool:
    """Deep-merge the L1 template into ``config`` in place; True when it changed.

    Merged rather than assigned so every key the user already wrote — model
    routing, credentials, his own ``compression`` tuning — survives the boot.
    """
    from plobi_cli.config import _deep_merge

    merged = _deep_merge(config, load_l1_profile_template())
    if merged == config:
        return False
    config.clear()
    config.update(merged)
    return True


def _enable_north_star_plugin(config: dict) -> bool:
    """List ``plobi-north-star`` in ``plugins.enabled`` (+ its entry) — opt-in flipped on."""
    plugins = config.get("plugins")
    if not isinstance(plugins, dict):
        plugins = {}
        config["plugins"] = plugins
    enabled = plugins.get("enabled")
    if not isinstance(enabled, list):
        enabled = []
        plugins["enabled"] = enabled
    changed = "plobi-north-star" not in enabled
    if changed:
        enabled.append("plobi-north-star")
    entries = plugins.get("entries")
    if not isinstance(entries, dict):
        entries = {}
        plugins["entries"] = entries
    entry = entries.get("plobi-north-star")
    if not isinstance(entry, dict):
        entry = {}
        entries["plobi-north-star"] = entry
    if not entry.get("enabled"):
        entry["enabled"] = True
        changed = True
    return changed


def _apply_l1_secretary_config(config: dict) -> bool:
    """Fold one config dict into L1 secretary shape. True when anything changed.

    One body, two callers (:func:`ensure_north_star_toolset` and
    :func:`ensure_l1_secretary_form`) so the plugin path and the boot path
    cannot disagree about what the shape is.
    """
    changed = _merge_l1_profile_template(config)
    changed = apply_l1_mid_toolsets(config) or changed
    changed = _enable_north_star_plugin(config) or changed
    return changed


def refresh_l2_identity_souls() -> int:
    """按**当前**契约重渲染名册里每个未归档 L2 的身份围栏，返回改写了几份。

    身份块只在 ``spawn()`` 时落盘（:func:`ensure_l2_identity_soul`），所以契约
    改版以后，已经在盘上的分身不会自己更新。桌面 / 网关每次启动过一遍，新契约
    才不会只对以后新建的 Agent 生效（用户 10-31：「L1 和 L2 干活第一反应都不是
    统筹，而是自己干活」——改的正是这段围栏）。只动围栏以内：围栏外人写的人设
    一个字不碰，内容没变就不写盘（幂等）。
    """
    try:
        registry = load_registry()
    except Exception as exc:
        logger.debug("plobi: L2 identity refresh skipped (registry): %s", exc)
        return 0
    updated = 0
    for entry in list(registry.agents.values()):
        if entry.archived:
            continue
        try:
            if ensure_l2_identity_soul(entry):
                updated += 1
        except Exception as exc:
            logger.debug(
                "plobi: L2 identity refresh skipped for %s: %s", entry.name, exc
            )
    return updated


def ensure_l1_secretary_form() -> bool:
    """Give the **default profile** its L1 secretary shape, inside its own home.

    Boot-time bootstrap for a chicken-and-egg: ``plobi-north-star`` is a bundled
    plugin and therefore opt-in, so a home that never listed it runs no
    ``register()`` — and ``register()`` is the only thing that ever narrowed L1's
    toolsets, applied the profile template, or wrote the SOUL fence. Such a
    machine gets a generalist holding ``terminal`` and no dispatch entry, which
    is exactly what was measured. Never creates a profile (裁定 6: L1 *is* the
    default profile, not a ``master`` one), never writes outside a home that
    already exists, and returns False once the shape is in place.

    It also refreshes every L2 identity fence (:func:`refresh_l2_identity_souls`)
    for the same reason one level down: those fences are written at spawn time,
    so a contract change would otherwise never reach an agent that already
    exists.
    """
    from plobi_cli.config import load_config, save_config
    from plobi_cli.agent_identity import get_active_profile_name
    from plobi_constants import get_plobi_home

    if get_active_profile_name() != "default":
        # 后端跑在某个分身的家目录里：L1 那把椅子不在这，写它就是把总秘书合同印到
        # 分身身上（裁定 42 §6(b)）。``ensure_north_star_toolset`` 同口径。
        return False
    if not get_plobi_home().is_dir():
        return False
    config = load_config()
    changed = _apply_l1_secretary_config(config)
    if changed:
        save_config(config)
    refreshed = refresh_l2_identity_souls()
    return ensure_l1_secretary_routing_soul() or changed or refreshed > 0


def ensure_north_star_toolset(*, save: bool = True) -> dict:
    """Open ``plobi_north_star`` on the **active** profile (usually default).

    Does not create a ``master`` profile (裁定 6). WP-BE-9 also mid-narrows
    L1 toolsets (drop terminal / session_search / command runners) and upserts
    soft-routing SOUL instructions. Idempotent and reversible via config lists.

    Reachable only once the plugin is enabled, which is why the boot-time door
    is :func:`ensure_l1_secretary_form` — bundled plugins are opt-in, so a home
    that never opted in would otherwise never write this shape at all.
    """
    from plobi_cli.config import load_config, save_config

    config = load_config()
    changed = _apply_l1_secretary_config(config)

    if changed and save:
        save_config(config)
    try:
        # 裁定 42 §42.2 / §6(b): default profile 是唯一 L1 总秘书的座位。这道闸之
        # 前任何 profile 只要启用本插件就被永久盖成总秘书（l2-agenda 那份 --clone
        # 的 SOUL 就是已经发作的实例）。非 default profile 不仅要跳过写入，还要把
        # 已经印上去的 L1 合同纠掉。判据与
        # ``plugins/plobi-north-star/l1_budget.py:115 is_l1_default_profile()``
        # 同源（get_active_profile_name() == L1_PROFILE_NAME），只是没有 agent 实例。
        from plobi_cli.agent_identity import get_active_profile_name

        if get_active_profile_name() == "default":
            ensure_l1_secretary_routing_soul()
        else:
            strip_l1_secretary_routing_soul()
    except Exception:
        logger.debug("plobi: L1 secretary SOUL routing upsert skipped", exc_info=True)
    return config


def load_registry(path: Path | str | None = None) -> AgentRegistry:
    return AgentRegistry.load(path)
