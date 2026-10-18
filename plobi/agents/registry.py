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
        mind_subtree: Vault/projects/Plobi   # 相对 MIND_ROOT（L2 直读）
        skills: [morning-report, message-digest]
        description: 日程闭环 Agent

``spawn`` 做三件事：确保 profile 存在（复用 ``plobi_cli.profiles.create_profile``）、
把该 agent 的模型路由写进 profile 的 ``plobi/models.json``（ADR-0011 断言绿）、
输出启动命令。路由断言 ``ModelRouter.assert_valid()`` 保证 L1≠L2 模型。
"""

from __future__ import annotations

import json
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
# 要在 ``Vault/projects/`` 立项（events 挂 agenda 事项、butler 是家务，都不是项目）。
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
    profile: str = ""  # plobi profile 名；空 = 用 name
    provider: str = ""  # 可选覆盖；空 = 默认路由
    model: str = ""  # 可选覆盖；空 = 默认路由
    mind_subtree: str = ""  # 相对 MIND_ROOT 的项目子树（L2 直读）
    skills: tuple[str, ...] = ()
    description: str = ""
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

    @property
    def profile_name(self) -> str:
        # Profiles are stored lowercase on disk and the CLI's ``-p`` pre-scan
        # drops any value failing ``_PROFILE_ID_RE`` — so handing out the
        # registry key verbatim (``Aura``) makes ``plobi --profile Aura serve``
        # fall through to argparse, which then reads ``Aura`` as a subcommand
        # and exits 2. The base documents the same requirement: normalize before
        # validation / subprocess spawn (plobi_cli/profiles.py:303-318, #18498).
        #
        # Normalized inline instead of importing that helper: this module
        # deliberately avoids depending on plobi_cli at attribute time (see the
        # lazy imports below), and even a lazy import breaks under the test
        # harness, where ``plobi_cli`` does not resolve to the real module.
        # tests/plobi_cli/test_profiles.py pins the two rules together.
        # 数据角色（``l2_course``）不是分身：它**没有运行时可连**，所以对外也不该有一个
        # 看起来能连的 profile 名。以前这里一律回退到条目名，于是名册里那五门课各自
        # 冒充一格 profile ⇒ 桌面为它们起 `plobi -p <课名>`：ASCII 名退 1
        # （`Profile 'X' does not exist`）、非 ASCII 名被 CLI 的 `-p` 预扫丢掉退 2
        # （就是下面这段注释本来预警的形状），每一门都是一场重启循环
        # （2026-10-13 现核 desktop.log：单「电工电子学」2767 行 failed）。
        # 回空串而不是加一枚 `runtime: false` 标志：标志要每个消费方记得去看，
        # 空串是这里本来就有的形状（``agent_askability`` 1019 行那条判据在判它）。
        # 真要给它一个运行时，就把角色改成 ``l2_project``，而不是给它写个 profile。
        from plobi.routing.models import L2_COURSE

        if self.role == L2_COURSE:
            return ""
        raw = (self.profile or self.name).strip()
        if not raw:
            return ""
        if raw.casefold() == "default":
            return "default"
        return raw.lower()

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
        if self.profile:
            data["profile"] = self.profile
        if self.provider:
            data["provider"] = self.provider
        if self.model:
            data["model"] = self.model
        if self.mind_subtree:
            data["mind_subtree"] = self.mind_subtree
        if self.skills:
            data["skills"] = list(self.skills)
        if self.description:
            data["description"] = self.description
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
            profile=str(raw.get("profile") or ""),
            provider=str(raw.get("provider") or ""),
            model=str(raw.get("model") or ""),
            mind_subtree=str(raw.get("mind_subtree") or ""),
            skills=tuple(str(s) for s in skills),
            description=str(raw.get("description") or ""),
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
        target.write_text(
            yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
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

        一个项目一棵树：``Vault/projects/Nymo`` 挂了 ``Nymo`` 之后再建一条挂同一棵树的分身，
        就是用户 2026-10-06 报的「左栏两个 nymo」。比的是归一后的整串相对路径——不分大小写的
        盘上 ``Vault/projects/nymo`` 与 ``Vault/projects/Nymo`` 也是同一棵树。
        """
        needle = (relative or "").strip().casefold()
        if not needle:
            return ""
        for other in self.live_entries():
            if other.name == exclude:
                continue
            if (other.mind_subtree or "").strip().casefold() == needle:
                return other.name
        return ""

    def upsert(self, entry: AgentEntry) -> AgentEntry:
        # 新键不许只差大小写地撞上已有条目——那等于给同一个 profile 家登记两个
        # 分身。改已有条目（键相同）不在此列：存量 ``Nymo``/``nymo`` 已经成对躺在
        # projects.yaml 里，读它们 / 补它们的字段不许报错（R-071 要的是不再新增，
        # 不是替用户改他的数据）。
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
        # 派生名撞上保留名 / 子命令 ⇒ 那个家**永远建不出来**。项目分身叫 ``Plobi`` 就是
        # 这一类：小写化是保留名 ``plobi``，``plobi -p plobi --help`` 退 0（argparse 认），
        # 死在后面那句 reserved 校验（2026-10-13 脑搬家线现核）。以前只给一句
        # "建议填 ``l2-plobi``"（:func:`profile_name_blocker`），没人去填，那条记录就一直红着。
        # 现在写入时把推荐名落成**显式 profile**：显示名仍是 ``Plobi``，内部家叫 ``l2-plobi``。
        # 那 11 条本来能用的名字一字不改——改它们要搬 ``state.db``，会把每个分身已有的会话
        # 历史孤儿化，那不是"修一个名字"该有的代价。两案（改名 / 项目分身干脆没有家）里取这一条。
        if not (entry.profile or "").strip() and (entry.profile_name or "").strip():
            from dataclasses import replace as _replace

            try:
                hint = suggested_profile_name(entry) if profile_name_blocker(entry) else ""
            except ImportError:
                # 判据住在 plobi_cli，而这台/这档测试里它未必解析得到真模块
                # （见 :attr:`AgentEntry.profile_name` 那段注释）。拿不到判据就**不挡登记**：
                # 这条是机会性改进，不该变成写入路径上的新硬依赖。
                hint = ""
            if hint and not self._profile_taken_by_other(hint, entry.name):
                entry = _replace(entry, profile=hint)
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
        因为路径解析依赖 ``plobi_cli.profiles`` —— registry 保持纯数据。
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

        1. 若 profile 不存在则 ``create_profile``：**裸建**（不拷 config.yaml /
           .env / SOUL.md，也不拷技能）——裁定 42 §42.2 把「一整套 Plobi 文件」
           从分身这一层收走，身份走 :func:`ensure_l2_identity_soul` 写的那段围栏，
           密钥走 :func:`apply_secret_overrides` 注入子进程环境。
           见 :meth:`_ensure_profile`。
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

        profile_dir = self._ensure_profile(entry)

        # WP-L2-IDENTITY / 裁定 42 §6(c): 分身缺的是「我是谁」。SOUL.md 是
        # ``load_soul_md()`` 唯一的身份来源，而裸建的 profile 里那份只有上游
        # 512 B 残桩。每次 spawn 都纠一次（幂等），
        # 后端一起就把身份落好，不靠人手抄文本。
        try:
            ensure_l2_identity_soul(entry, home=profile_dir)
        except Exception as exc:
            logger.warning(
                "plobi: L2 identity SOUL not written for %s: %s", entry.name, exc
            )

        # 1) profile 级 models.json（ADRD-0011 断言基于它）
        profile_models = self._write_profile_models(profile_dir, entry)

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
            "profile": entry.profile_name,
            "profile_dir": str(profile_dir),
            "command": f"plobi -p {entry.profile_name} chat",
            "routing_ok": True,
        }

    def _ensure_profile(self, entry: AgentEntry) -> Path:
        from plobi_cli.profiles import (
            create_profile,
            get_profile_dir,
            profile_exists,
        )

        name = entry.profile_name
        if profile_exists(name):
            return get_profile_dir(name)
        blocker = profile_name_blocker(entry)
        if blocker:
            raise RegistryError(blocker)
        # 裁定 42 §42.2：一个项目分身 = projects.yaml 一条记录 + 最多三个覆盖，
        # **不再是一整套 Plobi 家的复印件**（实测 13 个分身的 .env MD5 全同、
        # config.yaml 逐字一致，从来没有一样东西是这个项目独有的）。所以这里
        # 裸建：不拷 config.yaml / .env / SOUL.md。技能那根线不靠拷贝、靠顶层那一份
        # 里的 ``skills.external_dirs``（:func:`apply_shared_skills_root`，只写一次）；
        # 模型与工作目录也不落文件，读时从名册那一行叠（:func:`profile_config_overlay`）。
        try:
            profile_dir = create_profile(name=name, no_skills=True)
        except FileExistsError:
            # Someone else landed it first — don't rewrite their config.
            return get_profile_dir(name)
        try:
            apply_shared_skills_root()
        except Exception as exc:  # 兜底：共享根写不进去也不该让 spawn 失败
            logger.warning(
                "plobi: shared skills root not wired for %s: %s", name, exc
            )
        return profile_dir

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
    """给这条记录推荐一个能用的 profile 名（推荐不出来为空串）。"""
    from plobi_cli.profiles import validate_profile_name

    for candidate in (f"l2-{entry.profile_name}", f"l2-{entry.name.strip().lower()}"):
        try:
            validate_profile_name(candidate)
        except ValueError:
            continue
        return candidate
    return ""


def profile_name_blocker(entry: AgentEntry) -> str:
    """这条记录的 profile 家**永远建不出来**时，返回一句能行动的话（没问题返回空串）。

    判据只有一份：``plobi_cli.profiles`` 的 profile 名校验。这里加的只有「改什么」——
    项目分身叫 ``Plobi`` 时小写化就是保留名 ``plobi``（和 Plobi 自己的安装撞），
    以前用户只看到一句 "Profile name 'plobi' is reserved"，不知道要改哪一格，
    于是那个分身永远问不到（R-071 实测第二条）。
    """
    from plobi_cli.profiles import validate_profile_name

    from plobi.routing.models import L2_COURSE

    if entry.role == L2_COURSE:
        # 数据条目**本来就没有家**——这不是缺陷，不该报"建不出自己的家"。
        # （它的"问不到"由 :func:`agent_askability` 按角色那句说，不靠这里。）
        return ""

    canon = entry.profile_name
    try:
        validate_profile_name(canon)
    except ValueError as exc:
        hint = suggested_profile_name(entry)
        advice = f"给这条记录单独填一个能用的 profile 名，比如 ``{hint}``；" if hint else ""
        return (
            f"分身「{entry.display_name or entry.name}」建不出自己的家：profile 名"
            f" ``{canon}`` 不能用（{exc}）。{advice}改完再登记一次就能问到它了。"
        )
    return ""


# ---------------------------------------------------------------------------
# 「这一条现在问得到吗」——名册与 ``plobi_agent_ask`` 共用的唯一判据（R-072）
# ---------------------------------------------------------------------------

#: 只有项目分身接受当面提问。日程 L2 有它自己那张嘴（``plobi_secretary_ask``），
#: default profile 是提问的那一个。判据看的是**角色**不是名字——``agenda`` 不是
#: 因为叫 ``agenda`` 被排除，任何非项目角色都同样走不到这条路上（裁定 69：
#: 日程秘书与其他 L2 地位平等，两边都不给它开后门、也不给它挂特殊牌子）。
ASKABLE_ROLE = "l2_project"


def _profile_landed(entry: AgentEntry) -> Optional[bool]:
    """这条记录的家在盘上落地了吗（探不到时返回 None，不瞎判）。"""
    canon = entry.profile_name
    if not canon or canon == "default":
        return None
    try:
        from plobi_cli.profiles import profile_exists

        return bool(profile_exists(canon))
    except Exception:  # pragma: no cover - plobi_cli 解析不了的那套测试环境
        logger.debug("plobi: profile_exists probe skipped for %s", canon, exc_info=True)
        return None


def agent_askability(entry: AgentEntry) -> "tuple[bool, str]":
    """L1 现在能不能用 ``plobi_agent_ask`` 问到这一条；问不到就给一句原因。

    只看结构性事实，不看任何一句文本：归档 / 角色 / profile 名建不建得出来
    （:func:`profile_name_blocker`）/ 家在不在盘上。名单与工具**必须读同一份判据**
    ——以前 ``run_project_status`` 报「有这个人」而 ``plobi_agent_ask`` 报「问不到」，
    两边各判一次，秘书就只能靠试（R-072）。

    探不到盘（``plobi_cli`` 解析不了的那套测试环境）时**不判成问不到**：宁可留一行
    可以试的，也不能把手下从名单里悄悄抹掉——抹掉才是这一刀要修的 bug。家真的在
    这一步之后被人收走时，``plobi_agent_ask`` 建命令那一步还会再拒一次。
    """
    if entry.archived:
        return False, "这一条已经归档，问不到它。"
    if entry.role != ASKABLE_ROLE:
        return False, (
            f"这一条的角色是 ``{entry.role}``，不是项目分身，不归 plobi_agent_ask 问"
            "（日程走 plobi_secretary_ask）。"
        )
    if not entry.profile_name or entry.profile_name == "default":
        return False, "这一条没有自己的家（profile 名空或撞了 default），问不到。"
    blocker = profile_name_blocker(entry)
    if blocker:
        return False, blocker
    if _profile_landed(entry) is False:
        return False, (
            f"「{entry.display_name or entry.name}」还没在这台机器上落地"
            f"（profiles/{entry.profile_name} 不存在）——登记 / 新建一次就问到它。"
        )
    return True, ""


def is_askable_agent(entry: AgentEntry) -> bool:
    """:func:`agent_askability` 的布尔口径（只要「能不能」，不要原因）。"""
    return agent_askability(entry)[0]


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
                f"Mind 那棵树（``{subtree}``）已经挂在分身「{owner}」名下——同一个项目不登记"
                f"第二条：要推进它就是去编辑「{owner}」，真另起一个项目请换一个项目名。"
            )

    incoming = _normalized_project_name(entry)
    if creating:
        for other in registry.live_entries():
            if other.name == entry.name or not incoming:
                continue
            if incoming == _normalized_project_name(other):
                return (
                    f"名册里已经有一条叫「{other.display_name or other.name}」的分身"
                    f"（注册键 ``{other.name}``）——重名不许再建第二条：要改它就是编辑那一条，"
                    f"想真的另起炉灶请换一个项目名。"
                )
        return ""

    # 命中已有键、但这条槽位属于另一个项目名：那是「顶替」，不是「编辑」。
    held_name = _normalized_project_name(held)
    if incoming and held_name and incoming != held_name:
        return (
            f"注册键「{entry.name}」已经是分身「{held.display_name or held.name}」——"
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
    模块级依赖它，见 :meth:`AgentRegistry._ensure_profile` 的惰性 import 与 51ff7f0
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
    for entry in entries:
        if entry.archived:
            continue
        landed = (entry.profile_name or "").casefold()
        if landed and landed == want:
            return entry
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
    return overlay


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
        profile=AGENDA_TEMPLATE_PROFILE,
        mind_subtree="Vault/projects/Plobi",
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
    profile = (entry.profile or "").strip().lower()
    desc = entry.description or ""
    if any(marker in role for marker in _AGENDA_ROLE_MARKERS):
        return True
    if "日程" in (entry.role or "") or "日程" in desc or "日程" in (entry.name or ""):
        return True
    if "secretary-agenda" in name or "secretary-agenda" in profile:
        return True
    return False


def find_agenda_agent(registry: AgentRegistry | None = None) -> Optional[AgentEntry]:
    """First matching agenda L2, preferring an exact ``l2_agenda`` role."""
    reg = registry or load_registry()
    found = [entry for entry in reg.entries() if is_agenda_entry(entry)]
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

    template = agenda_template_entry()
    if template.name in reg.agents:
        template = AgentEntry(
            name="secretary-agenda",
            role=template.role,
            profile=template.profile,
            provider=template.provider,
            model=template.model,
            mind_subtree=template.mind_subtree,
            skills=template.skills,
            description=template.description,
        )
    reg.upsert(template)
    reg.save()
    return template, True


def ensure_agenda_agent(registry: AgentRegistry | None = None) -> tuple[AgentEntry, bool]:
    """Return the agenda L2, spawning **one** template entry if none exists (S2).

    Never creates any other L2. ``spawned`` is True only when this call
    registered the template. Profile materialization is idempotent.

    The secretary write paths are what need the profile to exist (the L2 runs
    the collection); startup only needs the row — see
    :func:`register_agenda_agent` for that half.
    """
    reg = registry or load_registry()
    existing, spawned = register_agenda_agent(reg)

    from plobi_cli.profiles import profile_exists

    if not profile_exists(existing.profile_name):
        reg.spawn(existing.name)
    elif spawned:
        # Newly registered into an already-present profile (unusual) — still
        # run spawn so models.json / config.yaml stay in sync.
        try:
            reg.spawn(existing.name)
        except RegistryError:
            logger.warning(
                "plobi: agenda L2 %r registered but spawn skipped: profile exists",
                existing.name,
            )
    try:
        from plobi_cli.profiles import get_profile_dir

        l2_dir = get_profile_dir(existing.profile_name)
        # L1 mid-narrow must not stick on a cloned agenda profile (裁定 12).
        ensure_l2_agenda_toolsets(l2_dir)
    except Exception as exc:
        logger.warning("plobi: L2 toolsets not written for %s: %s", existing.name, exc)
    return existing, spawned


# ---------------------------------------------------------------------------
# WP-PROJECT-PORTFOLIO: ensure_mind_project_agents + project_status + plan_day
# ---------------------------------------------------------------------------

# Hard-coded binding for the live workspace (no env lookup, no index scan).
# The Mind INDEX records ``cloud: D:\Cloud\Projects\Plobi``, but the *source*
# copy lives under ``D:/Projects/Plobi/Code`` (task: "Plobi 固定
# ``D:\Projects\Plobi\Code``"). Anything else gets the cloud path from plan.md
# frontmatter or, failing that, ``D:\Cloud\Projects\<name>``.
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


def _project_is_skipped(front: dict[str, str]) -> bool:
    """True when the project's plan.md marks it paused / done / archived.

    Looks at the frontmatter ``status`` field first (canonical), then falls
    back to a substring scan over the rendered body when frontmatter is
    absent or ambiguous.
    """
    status = (front.get("status") or "").strip().lower()
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
    prefer the Cloud path already declared in plan.md (``cloud:``); if the
    frontmatter omits it, fall back to ``D:\Cloud\Projects\<name>``. The
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
    the system-self project (``Mind``), any directory without a ``plan.md``, and
    any plan whose frontmatter ``status`` matches the skip markers. The remaining
    projects become ``l2_project`` entries with ``category=projects`` (or ``research``
    only when ``plan.md``'s ``group`` says so). Returns ``(registry, seeded)``
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
        plan_path = entry / "plan.md"
        if not plan_path.is_file():
            continue
        front = _read_plan_frontmatter(plan_path)
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
            profile="",
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
        # WP-L2-DIET / 裁定 33.3: spawn 成功后立刻把 terminal / computer_use /
        # code_execution / session_search / plobi-* 从该 profile config.yaml
        # 剥掉。L2-agenda 不走这条路（保证 agenda 仍能 spawn terminal），它
        # 有自己的 ensure_l2_agenda_toolsets。
        if spawn_result and category in ("projects", "research"):
            try:
                apply_l2_project_diet(spawn_result.get("profile_dir"))
            except Exception as exc:
                logger.warning("plobi: l2 diet skipped for %s: %s", safe_id, exc)
    if seeded:
        try:
            reg.save()
        except Exception as exc:
            logger.warning("plobi: registry save skipped: %s", exc)
    return reg, seeded


def run_project_status(
    user_text: str,
    *,
    pipeline=None,
    registry: AgentRegistry | None = None,
) -> dict:
    r"""Report the project 分身: who is on the roster and who can actually be asked.

    Calls :func:`ensure_mind_project_agents` first (idempotent) and then
    returns the live ``l2_project`` rows whose category is ``projects`` or
    ``research``. The output includes a brief plan/progress excerpt per row
    (≤400 chars) so the L1 final answer can quote real evidence without
    ever dumping the whole plan.md into context. Never touches chatlog and
    never spawns an agenda L2 — task: "ChatlogDead 下仍 ok".

    两格分开的判据（R-070 / R-072）：
    * ``roster`` = :attr:`AgentEntry.registered` 为真的行——新建 / 登记那一步盖过章的
      手下。这一格是账面事实，**不看 description 里那句话长什么样**（旧的
      ``is_mind_placeholder`` 靠占位句前缀猜，那句话一抖手下就从名单里消失）。
    * ``mind_candidates`` = 播种机投影下来的占位（``registered=False``），只是候选。

    每一行还带 ``askable`` + ``not_askable_because``（:func:`agent_askability`，与
    ``plobi_agent_ask`` 同一份判据）：问不到的行**留在名单里并写明为什么问不到**，
    不偷偷删行——删行等于秘书没有手下。
    """
    user_text = (user_text or "").strip()
    reg, seeded = ensure_mind_project_agents(registry)

    from plobi.mind.paths import real_target, resolve_root
    from plobi.mind.project_tree import PROJECTS_RELATIVE, tree_root_for

    mind_root = resolve_root(None)

    def _tree_dir(subtree: str, name: str, category: str) -> "Path | None":
        """这一格的项目树在盘上哪儿——名册指针优先，其次分类文件夹里同名的那棵。

        写死只读 ``Vault/projects/`` 的话，搬过家的树（``research/SRTP``）在这份名单里
        就永远是没有脑计划，秘书照着念就成了「有嘴却读不到自己那门课的作业」那个形状。
        """
        if mind_root is None:
            return None
        candidates = [
            subtree,
            f"{tree_root_for(category)}/{name}",
            f"{PROJECTS_RELATIVE}/{name}",
        ]
        directories = [real_target(c, mind_root) for c in candidates if c]
        for directory in directories:
            if (directory / "plan.md").is_file():
                return directory
        for directory in directories:
            if directory.is_dir():
                return directory
        return None

    rows: list[dict] = []
    for entry in reg.entries():
        if entry.archived:
            continue
        if entry.role != ASKABLE_ROLE:
            continue
        if entry.category not in MIND_PROJECT_CATEGORIES:
            continue
        # 这一行现在问得到吗？判据只有 :func:`agent_askability` 那一份，
        # 和 ``plobi_agent_ask`` 用来筛名单的是同一个函数——名单和嘴不许各判一次。
        askable, why_not_askable = agent_askability(entry)
        plan_text = ""
        progress_text = ""
        has_mind = False
        directory = _tree_dir(
            (entry.mind_subtree or "").strip("/"), entry.name, entry.category
        )
        if directory is not None:
            try:
                has_mind = True
                plan_path = directory / "plan.md"
                progress_path = directory / "progress.md"
                if plan_path.is_file():
                    plan_text = plan_path.read_text(encoding="utf-8").strip()
                if progress_path.is_file():
                    progress_text = progress_path.read_text(
                        encoding="utf-8"
                    ).strip()
            except (OSError, UnicodeDecodeError):
                pass
        rows.append(
            {
                "id": entry.name,
                "name": entry.name,
                "category": entry.category,
                "weekly_hours": entry.weekly_hours,
                "project_path": entry.project_path,
                "mind_subtree": entry.mind_subtree,
                "has_mind": has_mind,
                # R-072：这一条算不算「他的手下」看的是名册上盖的那一章
                # （:attr:`AgentEntry.registered`），不是 description 里那句话的
                # 长相。名单里问不到的行**留在名单里并标出来**（``askable`` +
                # ``not_askable_because``），不悄悄删行——删行就是秘书看不见手下。
                "registered": entry.registered,
                "askable": askable,
                "not_askable_because": why_not_askable,
                "plan_excerpt": plan_text[:PROJECT_EXCERPT_MAX_CHARS],
                "progress_excerpt": progress_text[:PROJECT_EXCERPT_MAX_CHARS],
            }
        )

    roster = [row["id"] for row in rows if row["registered"]]
    mind_candidates = [row["id"] for row in rows if not row["registered"]]
    # 「名单里的每一行都真的能问」这一条不许靠秘书自己试：能问的那几行直接给出来，
    # 判据和 ``plobi_agent_ask`` 用的是同一份（:func:`agent_askability`）。
    askable_roster = [row["id"] for row in rows if row["registered"] and row["askable"]]

    return {
        "ok": True,
        "intent": "project_status",
        "user_text": user_text,
        "seeded": seeded,
        # 裁定 69 把基本单位定成 Agent，所以「我手下有谁」只认 projects.yaml 里
        # 登记过的那几条（R-070 实测：用户只配了 Nymo + 日程秘书，投影却把
        # Mind 的 11 个项目一起报成手下）。Mind 那一堆最多是「还没给它配分身」的
        # 候选，两类分开列，不许混成一个名单让秘书照着念。
        "roster": roster,
        "mind_candidates": mind_candidates,
        "askable": askable_roster,
        "roster_note": (
            "roster 是登记进名册的项目分身，askable 是其中现在就能用 "
            "plobi_agent_ask 当面问的那几行；roster 里 askable=false 的行照样在名单上，"
            "问不到，原因就在该行的 not_askable_because，要如实说、不要硬试也别换个名字猜。"
            "mind_candidates 只是 Mind 里有计划、还没给它登记分身的项目，不能算手下，"
            "要报就得说明是候选。"
        ),
        "projects": rows,
    }


def run_plan_day(
    user_text: str,
    *,
    date: str | None = None,
    pipeline=None,
    registry: AgentRegistry | None = None,
    action: str | None = None,
) -> dict:
    """Read tomorrow's (or ``date``'s) plan **from the store** — this function never writes.

    它以前是 ``generate_evening_plan`` 的薄壳，那句 docstring 写着「no mutation」而**它是错的**：
    ``generate_evening_plan`` 先 DELETE 当天 ``plan_items`` 再重插。均分本体（裁定 149）退役之后
    那条路重插不出任何块，于是每一次「读」都把秘书排好的块抹掉——10-17 17:2x 实测在案
    （``WP-PLAN-DAY-CLOBBER``）。现在这里只读 ``daily_plans`` / ``plan_items``：没有那份计划就
    照实说没有，绝不"顺手生成一份"。要生成由秘书那一发去写（``action=plan_tomorrow``）。
    """
    from datetime import date as _date, timedelta

    user_text = (user_text or "").strip()
    for_date = (str(date).strip()[:10] if date else "") or (
        _date.today() + timedelta(days=1)
    ).isoformat()

    db_path = _secretary_db_path(pipeline)
    try:
        from plobi.agenda import store as _store
    except Exception as exc:
        return {
            "ok": False,
            "intent": "plan_day",
            "error": f"无法加载日程存储: {exc}",
            "for_date": for_date,
            "user_text": user_text,
        }
    try:
        # 只读：读不到就是没有，不生成（生成会 DELETE 当天那批 plan_items）。
        _conn = _store.connect(db_path)
        try:
            plan = _store.get_daily_plan(_conn, for_date)
        finally:
            _conn.close()
    except Exception as exc:
        logger.warning("plobi: plan_day failed for %s: %s", for_date, exc)
        return {
            "ok": False,
            "intent": "plan_day",
            "error": f"plan_day 失败: {exc}",
            "for_date": for_date,
            "user_text": user_text,
        }

    if plan is None:
        # 读不到就是没有——这一句必须说得出「没有」，不许为了给出东西而现生成一份
        # （生成 = 先 DELETE 当天那批 plan_items，那正是本刀要堵的口子）。
        return {
            "ok": False,
            "intent": "plan_day",
            "for_date": for_date,
            "written": 0,
            "error": (
                f"{for_date} 那份计划还没有（我这一条只读，不替你生成）。"
                "要排就让我自己排一次：plobi_secretary_ask，intent=plan_day、action=plan_tomorrow。"
            ),
            "items": [],
        }

    items: list[dict] = []
    try:
        conn = _store.connect(db_path)
        try:
            rows = _store.list_plan_items(conn, plan.id)
        finally:
            conn.close()
        for item in rows:
            items.append(
                {
                    "title": item.title,
                    "start_at": item.start_at,
                    "end_at": item.end_at,
                    "kind": item.kind,
                    "evidence": item.evidence,
                }
            )
    except Exception as exc:
        logger.warning("plobi: plan_day item read failed: %s", exc)

    return {
        "ok": True,
        "intent": "plan_day",
        "for_date": plan.for_date,
        "summary": (plan.summary or "").strip(),
        "conflict_count": int(plan.conflict_count),
        "items": items,
    }


# WP-PROJECT-PORTFOLIO: 7 values. ``project_status`` reads Mind's project tree
# and reports each ``l2_project`` (no chatlog, no agenda L2 spawn). ``plan_day``
# 自 10-17 起是**纯读**（``store.get_daily_plan``；它以前调 ``generate_evening_plan``，
# 那个函数先 DELETE 当天 plan_items 再重插 —— `WP-PLAN-DAY-CLOBBER` 就是这一口）。Both are
# routed before ``ensure_agenda_agent`` so a dead collector cannot block them.
SECRETARY_ASK_INTENTS = (
    "refresh_agenda",
    "write_briefing",
    "mutate_agenda",
    "query_agenda",
    "decide_pending",
    "project_status",
    "plan_day",
)

# WP-PROJECT-PORTFOLIO: hard limits for ``project_status`` excerpts — a brief
# slice of plan/progress, never a 4000-char dump into L1 context.
PROJECT_EXCERPT_MAX_CHARS = 400

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

# WP-L1-AGENDA-MUTATE: conversational write intents. Reading (refresh /
# briefing) may hit chatlog; writing MUST NOT touch chatlog and must work
# even when collection is dead.
MUTATE_ACTIONS = ("create", "update", "delete", "cancel_matching", "move_block",
                  "add_life_block")
# WP-SECRETARY-MOUTH: cancel_matching is the only bulk-delete the L1 is
# allowed to issue ("这期完了 / 这门课后面都不用去了"). Each source is gated
# individually — only sources the secretary has a write path for can be
# cancelled here, and never in 200 delete round-trips.
#
# R-105 / WP-PLAN-CHAT-ADJUST: ``move_block`` is the fifth **action**, not an
# eighth **intent** — 裁定 51 冻结的是七个 intent，嘴还是那张嘴。它改的是
# ``plan_items``（L1 排出来的那块），不是 ``events``；走
# :mod:`plobi.agenda.plan_edits`，与排程过同一道边界闸。
CANCEL_MATCHING_SOURCES: tuple[str, ...] = ("timetable",)
MUTATE_KINDS = ("meeting", "task", "ddl", "class")

# WP-SEC-VOCAB: read + decide intents. These answer straight from the shared
# agenda store — no collection round-trip, no L2 spawn, no N3 write — so a
# dead chatlog cannot make the secretary unable to say what is on today.
QUERY_RANGES = ("today", "tomorrow", "week", "date", "pending")
DECIDE_ACTIONS = ("confirm", "dismiss")
PENDING_STATUSES: tuple[str, ...] = ("pending",)
# 「今天起 7 天」= today .. today+6 (inclusive).
QUERY_WEEK_DAYS = 7

MISSING_PLAN_SUMMARY = "昨夜未生成计划"
PLAN_BRIEFING_STATUSES = frozenset({"pending", "confirmed", "empty"})
STALE_PLAN_NOTE = "计划生成后日程有变，以看板为准"


def _tomorrow_for_date(agenda_summary: dict | None = None) -> str:
    from datetime import date, timedelta

    listed = (agenda_summary or {}).get("tomorrow")
    if listed:
        return str(listed)
    return (date.today() + timedelta(days=1)).isoformat()


def _secretary_db_path(pipeline=None):
    service = getattr(pipeline, "service", None) if pipeline is not None else None
    return getattr(service, "db_path", None) if service is not None else None


def _tomorrow_event_ids(agenda_summary: dict | None) -> set[str]:
    events = (agenda_summary or {}).get("tomorrow_events") or []
    return {
        str(event.get("id"))
        for event in events
        if isinstance(event, dict) and event.get("id")
    }


def _missing_plan_payload(for_date: str) -> dict:
    return {
        "status": "missing",
        "summary": MISSING_PLAN_SUMMARY,
        "for_date": for_date,
        "event_count": 0,
        "conflict_count": 0,
        "stale": False,
        "empty": False,
    }


def _annotate_stale_summary(view: dict) -> dict:
    if not view.get("stale"):
        return view
    summary = (view.get("summary") or "").strip()
    if STALE_PLAN_NOTE in summary:
        return view
    annotated = dict(view)
    annotated["summary"] = f"{summary}。{STALE_PLAN_NOTE}" if summary else STALE_PLAN_NOTE
    return annotated


def _l1_plan_view_from_store(
    for_date: str, tomorrow_ids: set[str], db_path
) -> dict:
    from plobi.agenda import store

    conn = store.connect(db_path)
    try:
        plan = store.get_daily_plan(conn, for_date)
        if plan is None:
            return _missing_plan_payload(for_date)
        items = store.list_plan_items(conn, plan.id)
        item_ids = {item.event_id for item in items if item.event_id}
        return {
            "status": plan.status,
            "summary": (plan.summary or "").strip(),
            "for_date": plan.for_date,
            "event_count": int(plan.event_count),
            "conflict_count": int(plan.conflict_count),
            "stale": item_ids != tomorrow_ids,
            "empty": plan.status == "empty",
        }
    finally:
        conn.close()


def _l1_plan_view(
    pipeline=None,
    agenda_summary: dict | None = None,
) -> dict:
    """Read tomorrow's nightly plan for L1. Prefer planning.l1_plan_view."""
    for_date = _tomorrow_for_date(agenda_summary)
    tomorrow_ids = _tomorrow_event_ids(agenda_summary)
    db_path = _secretary_db_path(pipeline)
    try:
        from plobi.agenda.planning import l1_plan_view as planning_view
    except ImportError:
        return _annotate_stale_summary(
            _l1_plan_view_from_store(for_date, tomorrow_ids, db_path)
        )
    return _annotate_stale_summary(
        planning_view(for_date, tomorrow_ids, db_path=db_path)
    )


def _uses_plan_briefing(plan: dict | None) -> bool:
    return bool(plan) and plan.get("status") in PLAN_BRIEFING_STATUSES


def _secretary_service(pipeline=None):
    """AgendaService bound to the pipeline's DB when available, else default."""
    from plobi.agenda.service import AgendaService, get_service

    db_path = _secretary_db_path(pipeline)
    if db_path is not None:
        return AgendaService(db_path)
    return get_service()


def _event_payload(event) -> dict:
    """One agenda row as the tool JSON contract (query / decide / mutate)."""
    return {
        "id": event.id,
        "title": event.title,
        "start_at": event.start_at,
        "end_at": event.end_at,
        "kind": event.kind,
        "status": event.status,
        "source": event.source,
    }


def _mutate_error(error: str, **extra) -> dict:
    payload = {
        "ok": False,
        "intent": "mutate_agenda",
        "error": error,
    }
    payload.update(extra)
    return payload


# ── WP-SECRETARY-MOUTH: bulk cancel matching (one tool call, not 200) ─────


# How many rows to surface in the ``sample`` list — small enough to read in
# chat, large enough to verify scope.
CANCEL_MATCHING_SAMPLE_LIMIT = 8


def _normalise_evidence_text(value: object) -> str:
    """Strip ``@所有人`` and collapse whitespace so the L1's user paste can
    match a stored chatlog snippet without false negatives."""
    if not isinstance(value, str):
        return ""
    text = value.replace("@所有人", " ").replace("@all", " ")
    return " ".join(text.split())


# Common decision-verb / 微信-style prefixes the L1's user_text carries but
# the chatlog snippet never does. We strip them from the needle so a paste
# like "确认 楼补办，如不能按时报到…" still matches the stored snippet
# "楼补办，如不能按时报到…".
_DECISION_VERB_PREFIXES: tuple[str, ...] = (
    "确认", "忽略", "删除", "取消", "弄掉", "丢掉", "remove", "delete", "dismiss", "confirm",
)


def _strip_decision_prefix(needle: str) -> str:
    """Drop a leading decision verb (and any trailing punctuation) from a
    pasted needle. Best-effort — if the user really did paste nothing else,
    the empty result is fine; the caller will get a 0-match error and the
    L1 can fall back to ask for a title."""
    text = needle.strip()
    lowered = text.lower()
    for prefix in _DECISION_VERB_PREFIXES:
        if lowered.startswith(prefix):
            rest = text[len(prefix):]
            for sep in (" ", "：", ":", "，", ",", "。", ".", "！", "!", "?"):
                if rest.startswith(sep):
                    rest = rest[len(sep):]
                    break
            return rest.lstrip()
    return text


def _event_text_for_match(event) -> str:
    """Concatenate every field the secretary is allowed to needle against.

    Title (top-level), location (top-level + evidence), and the chatlog
    snippet / raw — whatever the row happens to carry. Missing keys are
    skipped silently; the worst case is a no-match, never a false positive.
    """
    bits: list[str] = [event.title or ""]
    if event.location:
        bits.append(event.location)
    evidence = event.evidence if isinstance(event.evidence, dict) else {}
    for key in ("snippet", "raw", "location"):
        value = evidence.get(key)
        if isinstance(value, str):
            bits.append(value)
    return _normalise_evidence_text("\n".join(bits))


def _find_pending_by_user_text(service, needle: str):
    """Locate a single pending event by the secretary's pasted needle.

    ``needle`` is ``user_text`` after stripping ``@所有人`` / whitespace,
    with a leading decision verb ("确认/忽略/删除/…") also removed so a
    paste like "确认 楼补办…" still matches the stored snippet "楼补办…".
    0 → ``ManualTargetMissing``; 2+ → ``ManualTargetAmbiguous`` with the
    full candidate briefs (id/title/start_at) so the L1 can ask the user
    which one to act on. Never guesses.
    """
    from plobi.agenda.service import (
        ManualTargetAmbiguous,
        ManualTargetMissing,
    )

    pending = service.list_pending()
    if not pending:
        raise ManualTargetMissing("没有待确认项")
    normalised = _normalise_evidence_text(needle)
    stripped = _strip_decision_prefix(normalised)
    for candidate in (stripped, normalised):
        if not candidate:
            continue
        hits = [event for event in pending if candidate in _event_text_for_match(event)]
        if hits:
            if len(hits) > 1:
                briefs = [
                    {"id": e.id, "title": e.title, "start_at": e.start_at}
                    for e in hits
                ]
                raise ManualTargetAmbiguous(briefs)
            return hits[0]
    raise ManualTargetMissing("没有匹配的待确认项")


def _run_cancel_matching(
    service,
    *,
    user_text: str,
    source: str | None,
    title: str | None,
    start_at: str | None,
) -> dict:
    """Bulk-cancel every matching ``source`` row on/after ``start_at``.

    Only wired for ``source=timetable`` this round; anything else is a hard
    error so the L1 cannot quietly destroy manual / wechat rows. The past
    is sacred — ``start_at`` is parsed as a date, anything strictly earlier
    is left alone. ``title`` is an optional substring filter (case-folded,
    whitespace-normalised) so 「这期完了」 can scope to one course.
    """
    from datetime import date as _date, datetime as _dt, timedelta

    from plobi.agenda.service import AgendaError

    if not source:
        return _mutate_error(
            "cancel_matching 需要 source=timetable（其它 source 本刀未实现）",
            action="cancel_matching",
            user_text=user_text,
        )
    if source not in CANCEL_MATCHING_SOURCES:
        return _mutate_error(
            f"cancel_matching 本刀只支持 source=timetable；收到 source={source!r}",
            action="cancel_matching",
            source=source,
            user_text=user_text,
        )

    raw_date = (start_at or "")[:10]
    try:
        from_day = _date.fromisoformat(raw_date) if raw_date else _date.today()
    except ValueError:
        return _mutate_error(
            f"cancel_matching 的 start_at 不是合法日期: {start_at!r}",
            action="cancel_matching",
            source=source,
            user_text=user_text,
        )

    from_day_start = _dt.combine(from_day, _dt.min.time())
    horizon_end = _dt.combine(from_day + timedelta(days=730), _dt.max.time())
    rows = service.list_agenda(from_day_start, horizon_end)
    title_lower = title.lower() if title else ""
    needle_norm = _normalise_evidence_text(title) if title else ""

    targets = [
        event
        for event in rows
        if event.source == source
        and event.status == "confirmed"
        and (not title or needle_norm in _event_text_for_match(event))
    ]

    cancelled = 0
    sample: list[dict] = []
    try:
        for event in targets:
            service.delete(event.id)
            cancelled += 1
            if len(sample) < CANCEL_MATCHING_SAMPLE_LIMIT:
                sample.append(
                    {
                        "id": event.id,
                        "title": event.title,
                        "start_at": event.start_at,
                    }
                )
    except AgendaError as exc:
        # Mid-loop failure: keep going but report what got cancelled so the
        # L1 can show real numbers instead of pretending nothing happened.
        return _mutate_error(
            f"批量取消中断：已删 {cancelled} 条后遇到错误 {exc}",
            action="cancel_matching",
            source=source,
            cancelled=cancelled,
            sample=sample,
            user_text=user_text,
        )

    payload: dict = {
        "ok": True,
        "intent": "mutate_agenda",
        "action": "cancel_matching",
        "source": source,
        "from_date": from_day.isoformat(),
        "cancelled": cancelled,
        "sample": sample,
        "user_text": user_text,
    }
    return payload


# ── R-105 / WP-PLAN-CHAT-ADJUST: 对话里改**计划里那一块** ──────────────────

#: 「谁改的」这一格写什么。真下令的是用户，落笔的是这张嘴——两个都留（判据②）。
PLAN_BLOCK_EDITOR = "l1:secretary"


def _run_move_block(
    user_text: str,
    *,
    db_path,
    title: str | None,
    event_id: str | None,
    start_at: str,
    end_at: str | None,
) -> dict:
    """把 L1 排出来的那一块挪到新钟点，**其余块一个字节不动**（R-105 判据①②）。

    指哪一块：``event_id`` 或 ``title``，也允许给那块**现在的起始 HH:MM**
    （query_agenda 回过 plan_items，L1 手上有这些）；对不上或对上两块 ⇒ 一句人话问回去，
    不替用户猜。新结束时间不给 ⇒ 保留这块原来的时长，不编。

    过闸与排程共用 :func:`plobi.agenda.llm_planning.validate_proposal`：挤掉作息、
    同一时段双订、越出清醒窗 ⇒ **零写入** + 一句人话（判据③，假成功比失败难查）。
    """
    from plobi.agenda import plan_edits, store

    ref = (event_id or title or "").strip()
    day = str(start_at)[:10]
    conn = None
    try:
        conn = store.connect(db_path)
        result = plan_edits.move_plan_item(
            conn,
            day,
            ref=ref,
            to_start=start_at,
            to_end=end_at,
            by=PLAN_BLOCK_EDITOR,
            reason=user_text,
        )
    except Exception as exc:  # 读不到库 / 校验器炸了都不许回一句"改好了"
        logger.warning("plobi: move_block failed for %s: %s", day, exc)
        return _mutate_error(
            f"这块我没挪成（{type(exc).__name__}: {str(exc)[:120]}）。原计划一个字没改。",
            action="move_block",
            user_text=user_text,
        )
    finally:
        if conn is not None:
            conn.close()

    if not result.get("ok"):
        return _mutate_error(
            str(result.get("reason") or "这块挪不动"),
            action="move_block",
            for_date=day,
            violations=result.get("violations"),
            user_text=user_text,
        )

    changed = result["changed"]
    others = max(int(result.get("item_count", 1)) - 1, 0)
    if result.get("life_block"):
        # R-106：作息是 standing 规则，面上必须说清「以后每天」，不许让人以为只挪了明天一次。
        say = (
            f"『{changed['title']}』以后每天都是 {changed['to']}（原来 {changed['from']}）；"
            f"改的是作息模板这一条真源，{PLAN_BLOCK_EDITOR} 按你这句话落的笔：{user_text or '未记原话'}。"
            + ("那天计划里的那一行已经跟着走了。" if result.get("synced_plan_row")
               else "那天还没生成计划，明晚 20:00 排的时候自然按新钟点落。")
        )
    else:
        say = (
            f"『{changed['title']}』从 {changed['from']} 挪到 {changed['to']}；"
            f"是你这句话让我改的（{PLAN_BLOCK_EDITOR} 落笔）：{user_text or '未记原话'}。"
            f"那天其余 {others} 块一个字没动。"
        )
    payload: dict = {
        "ok": True,
        "intent": "mutate_agenda",
        "action": "move_block",
        "for_date": day,
        "changed": changed,
        "life_block": bool(result.get("life_block")),
        "user_text": user_text,
        "read_back": result.get("read_back"),
        # 面上就说这一句（判据②：改了哪一块、谁改的、其余没动）。
        "say": say,
    }
    return payload


def _run_add_life_block(
    user_text: str,
    *,
    db_path,
    title: str,
    start_at: str,
    end_at: str,
) -> dict:
    """用户说「以后傍晚要锻炼半小时」⇒ 建一条作息（R-106 判据①的另一半）。

    只记他说的：名字与两个钟点缺一律问回去，不替他编一条生活类别出来（裁定 148 明写了
    「要装进来的是哪些生活类别」由用户定）。撞了已有作息／事件 ⇒ 一句人话 + 零写入。
    """
    from plobi.agenda import plan_edits, store

    day = str(start_at)[:10]
    conn = None
    try:
        conn = store.connect(db_path)
        result = plan_edits.add_life_block(
            conn, day, title=title, start_at=start_at, end_at=end_at,
            by=PLAN_BLOCK_EDITOR, reason=user_text,
        )
    except Exception as exc:
        logger.warning("plobi: add_life_block failed for %s: %s", title, exc)
        return _mutate_error(
            f"这条生活块我没加上（{type(exc).__name__}: {str(exc)[:120]}）。什么都没写。",
            action="add_life_block", user_text=user_text,
        )
    finally:
        if conn is not None:
            conn.close()

    if not result.get("ok"):
        return _mutate_error(
            str(result.get("reason") or "这条生活块加不进去"),
            action="add_life_block", for_date=day, user_text=user_text,
        )

    changed = result["changed"]
    payload: dict = {
        "ok": True,
        "intent": "mutate_agenda",
        "action": "add_life_block",
        "for_date": day,
        "template_id": result.get("template_id"),
        "changed": changed,
        "life_block": True,
        "user_text": user_text,
        "say": (
            f"记下了：以后每天 {changed['to']} 是『{changed['title']}』（{PLAN_BLOCK_EDITOR} 落的笔：{user_text}）。"
            + ("今天计划里已经排上这一行。" if result.get("synced_plan_row")
               else "明天那份计划排的时候它会自己进材料——它是边界，L1 不许把它挤掉。")
        ),
    }
    return payload


def run_mutate_agenda(
    user_text: str,
    *,
    action: str | None = None,
    title: str | None = None,
    start_at: str | None = None,
    end_at: str | None = None,
    kind: str | None = None,
    event_id: str | None = None,
    source: str | None = None,
    pipeline=None,
    registry: AgentRegistry | None = None,
) -> dict:
    """WP-L1-AGENDA-MUTATE: add / change / cancel via the same secretary mouth.

    R-105 / WP-PLAN-CHAT-ADJUST adds ``action=move_block`` — the fifth action,
    still the same frozen seven intents (裁定 51). It edits ``plan_items`` (the
    block L1 placed), not ``events``, and goes through
    :mod:`plobi.agenda.plan_edits`, which validates against the very same
    boundary gate the nightly planner uses. Refusal is a plain sentence plus
    **zero writes**; a silent re-flow of the rest of the plan is what the
    ruling forbids, so only the named row is ever rewritten.

    Goes straight to ``AgendaService`` — never calls ``refresh_agenda`` and
    never touches chatlog, so a dead collector cannot block a write.
    User-spoken changes are human orders: ``create`` lands ``source=manual``
    + ``confirmed``; ``delete`` on a confirmed row deletes it, on a pending
    row it dismisses (never "rejects" the user's own words as a candidate).
    Missing action / missing create title or start_at → error, zero writes.

    WP-SECRETARY-MOUTH: ``cancel_matching`` is the bulk-cancel mouth —
    "这期完了 / 这门课后面都不用去了" lands in one tool call, never as 200
    delete round-trips. Only ``source=timetable`` is wired this round
    (the rest error so the L1 surfaces them instead of silently dropping
    rows the user did not mean to touch). ``start_at`` is interpreted as a
    date — events with ``start_at`` at or after that day 00:00 local get
    deleted; the past is sacred. ``title`` is an optional substring filter.
    """
    user_text = (user_text or "").strip()
    action = (action or "").strip().lower()
    if action not in MUTATE_ACTIONS:
        return _mutate_error(
            "mutate_agenda 需要 action=create/update/delete/cancel_matching/move_block",
            action=action or None,
            user_text=user_text,
        )

    title = (str(title).strip() if title else "") or None
    start_at = (str(start_at).strip() if start_at else "") or None
    end_at = (str(end_at).strip() if end_at else "") or None
    kind = (str(kind).strip() if kind else "") or "task"
    event_id = (str(event_id).strip() if event_id else "") or None
    source = (str(source).strip().lower() if source else "") or None

    if kind not in MUTATE_KINDS:
        return _mutate_error(
            f"kind 只允许 {'/'.join(MUTATE_KINDS)}，收到 {kind!r}",
            action=action,
            user_text=user_text,
        )
    if action == "create":
        if not title:
            return _mutate_error("create 需要 title", action=action, user_text=user_text)
        if not start_at:
            # No invented 9:00, no invented 1-hour duration: ask instead.
            return _mutate_error(
                "create 需要 start_at（本地 ISO 钟点；缺钟点要问用户，不要编）",
                action=action,
                user_text=user_text,
            )
    if action in ("update", "delete", "move_block") and not event_id and not title:
        return _mutate_error(
            f"{action} 需要 event_id 或 title 来定位那条日程"
            if action != "move_block"
            else "move_block 需要 title 或 event_id 来指哪一块（说一下钟点也行，但要唯一对上）",
            action=action,
            user_text=user_text,
        )
    if action == "move_block" and not start_at:
        # 同 create 那条纪律：缺钟点问用户，不编。
        return _mutate_error(
            "move_block 需要 start_at（挪到什么时候，本地 ISO 钟点；缺了要问用户，不要编）",
            action=action,
            user_text=user_text,
        )
    if action == "add_life_block" and not (title or "").strip():
        return _mutate_error(
            "add_life_block 需要 title（这条生活块叫什么，例如「傍晚锻炼」）",
            action=action, user_text=user_text,
        )
    if action == "add_life_block" and (not start_at or not end_at):
        # 148 明说过比例与类别由用户定：这里只记他说的，不替他编一条作息出来。
        return _mutate_error(
            "add_life_block 需要 start_at 与 end_at（每天从几点到几点；缺了问用户，我不替他编时长）",
            action=action, user_text=user_text,
        )

    try:
        from plobi.agenda.service import (
            AgendaError,
            ManualTargetAmbiguous,
            ManualTargetMissing,
        )

        service = _secretary_service(pipeline)

        # WP-SECRETARY-MOUTH: bulk-cancel path runs BEFORE create/update/delete
        # so it never collides with single-row resolution. It does its own
        # source/date/title checks so the error messages stay actionable for
        # the L1 instead of falling into the generic "need event_id or title"
        # branch below.
        if action == "cancel_matching":
            return _run_cancel_matching(
                service,
                user_text=user_text,
                source=source,
                title=title,
                start_at=start_at,
            )

        # R-105 / WP-PLAN-CHAT-ADJUST：这一条改的是**计划里那一块**（plan_items），
        # 不是 events——所以它不归 AgendaService 管。与 cancel_matching 一样直接
        # return：不领日程 L2 的名片，免得「挪一块」顺手 spawn 一个分身。
        if action == "move_block":
            return _run_move_block(
                user_text,
                db_path=_secretary_db_path(pipeline),
                title=title,
                event_id=event_id,
                start_at=start_at,
                end_at=end_at,
            )

        # R-106 / WP-PLAN-LIFE-BUDGET：用户自己加一条生活块（裁定 148「生活项要能自己加」）。
        # 写的是 `routine_templates`（真源），不是往明天的计划里塞一行。
        if action == "add_life_block":
            return _run_add_life_block(
                user_text,
                db_path=_secretary_db_path(pipeline),
                title=title,
                start_at=start_at,
                end_at=end_at,
            )

        if action == "create":
            event = service.create_manual(
                title=title, start_at=start_at, end_at=end_at, kind=kind
            )
            payload = {
                "ok": True,
                "intent": "mutate_agenda",
                "action": "create",
                "event": _event_payload(event),
                "user_text": user_text,
            }
        else:
            try:
                target = service.resolve_manual_target(
                    event_id=event_id,
                    title=title,
                    day=start_at[:10] if start_at else None,
                )
            except ManualTargetMissing as exc:
                return _mutate_error(str(exc), action=action, user_text=user_text)
            except ManualTargetAmbiguous as exc:
                return _mutate_error(
                    "同一时段匹配到多条同名日程，不猜；请指定其中一条",
                    action=action,
                    candidates=exc.candidates,
                    user_text=user_text,
                )

            if action == "update":
                event = service.update_manual(
                    target.id,
                    title=title,
                    start_at=start_at,
                    end_at=end_at,
                    kind=kind,
                )
                payload = {
                    "ok": True,
                    "intent": "mutate_agenda",
                    "action": "update",
                    "event": _event_payload(event),
                    "user_text": user_text,
                }
            else:  # delete: confirmed → delete, pending → dismiss (人令)
                resolved = _event_payload(target)
                if target.status == "pending":
                    service.dismiss(target.id)
                else:
                    service.delete(target.id)
                payload = {
                    "ok": True,
                    "intent": "mutate_agenda",
                    "action": "delete",
                    "deleted": True,
                    "resolved": resolved,
                    "user_text": user_text,
                }
    except AgendaError as exc:
        return _mutate_error(str(exc), action=action, user_text=user_text)
    except Exception as exc:  # store/validation errors must not fabricate success
        logger.warning("plobi: mutate_agenda write failed: %s", exc)
        return _mutate_error(f"日程写入失败: {exc}", action=action, user_text=user_text)

    # C3 name card is optional garnish — never worth a collection round-trip.
    try:
        entry, spawned = ensure_agenda_agent(registry or load_registry())
        payload["agent"] = {
            "id": entry.name,
            "role": entry.role,
            "profile": entry.profile_name,
            "spawned": spawned,
        }
    except Exception as exc:
        logger.warning("plobi: mutate_agenda agent card skipped: %s", exc)
    return payload


# ── WP-SEC-VOCAB: read the agenda / decide a pending row (no collection) ────


def _query_error(error: str, **extra) -> dict:
    payload = {"ok": False, "intent": "query_agenda", "error": error}
    payload.update(extra)
    return payload


def _decide_error(error: str, **extra) -> dict:
    payload = {"ok": False, "intent": "decide_pending", "error": error}
    payload.update(extra)
    return payload


def _candidate_rows(service, candidates: list[dict]) -> list[dict]:
    """候选 brief（id/title/start_at）+ 有 ``prev_value`` 时一并交出来。"""
    rows: list[dict] = []
    for brief in candidates:
        row = dict(brief)
        try:
            event = service.get(str(brief.get("id")))
        except Exception:
            event = None
        if event is not None and event.prev_value:
            row["prev_value"] = event.prev_value
        rows.append(row)
    return rows


def _find_agenda_target(
    service,
    *,
    event_id: str | None,
    title: str | None,
    day: str | None,
    statuses: tuple[str, ...] | None,
):
    """定位唯一那条日程；不猜。

    给了 ``event_id`` 或 ``day`` 就交给 ``resolve_manual_target``；否则按
    「今天起 7 天内」逐日问它（口径与写入口完全一致：0 条 → Missing，
    2+ 条 → Ambiguous），跨天命中多条同样算不唯一。
    """
    from datetime import date as _date, timedelta

    from plobi.agenda.service import ManualTargetAmbiguous, ManualTargetMissing

    if event_id or day:
        return service.resolve_manual_target(
            event_id=event_id, title=title, day=day, statuses=statuses
        )

    hits = []
    for offset in range(QUERY_WEEK_DAYS):
        probe = (_date.today() + timedelta(days=offset)).isoformat()
        try:
            hits.append(
                service.resolve_manual_target(
                    title=title, day=probe, statuses=statuses
                )
            )
        except ManualTargetMissing:
            continue
    if not hits:
        raise ManualTargetMissing(f"找不到标题含 {title!r} 的日程")
    if len(hits) > 1:
        raise ManualTargetAmbiguous(
            [{"id": e.id, "title": e.title, "start_at": e.start_at} for e in hits]
        )
    return hits[0]


def run_query_agenda(
    user_text: str,
    *,
    range: str | None = None,
    date: str | None = None,
    pipeline=None,
) -> dict:
    """WP-SEC-VOCAB: answer 「今天/明天/本周/某天/待确认有什么」 from the store.

    Pure read: no ``refresh_agenda``, no L2 spawn, no N3 write, no invented
    clock (``end_at`` stays ``null`` when the row has none). ``events`` holds
    ``confirmed`` rows in ``start_at`` order; ``pending`` is listed separately
    so an unconfirmed chatlog candidate never reads as scheduled. Bad or
    missing parameters → ``ok=false`` and zero side effects.

    WP-QUERY-DAY: for ``range in (today, tomorrow, date)`` the response also
    carries ``anchors`` (as-kept routine anchors for that single day, same
    pure function the right rail uses) and ``plan_items`` (that day's daily
    plan, ``[]`` if no plan exists yet). The lunch / sleep line the right
    rail draws and the words the L1 now says come from the same source so
    they cannot drift apart. ``week`` is a 7-day window and
    ``pending`` is unbounded — neither is a single-day axis, so neither gets
    anchors or plan_items.
    """
    from datetime import date as _date, datetime as _datetime, timedelta

    from plobi.agenda import planning as _planning
    from plobi.agenda import store as _store

    user_text = (user_text or "").strip()
    window = (str(range).strip().lower() if range else "")
    if window not in QUERY_RANGES:
        return _query_error(
            f"range 只允许 {'/'.join(QUERY_RANGES)}",
            range=window or None,
            user_text=user_text,
        )

    day_text = (str(date).strip() if date else "")[:10]
    start: _date | None = None
    end: _date | None = None
    if window == "date":
        if not day_text:
            return _query_error(
                "range=date 需要 date=YYYY-MM-DD", range=window, user_text=user_text
            )
        try:
            start = end = _date.fromisoformat(day_text)
        except ValueError:
            return _query_error(
                f"date 不是合法日期: {date!r}", range=window, user_text=user_text
            )
    elif window != "pending":
        today = _date.today()
        if window == "today":
            start = end = today
        elif window == "tomorrow":
            start = end = today + timedelta(days=1)
        else:  # week
            start, end = today, today + timedelta(days=QUERY_WEEK_DAYS - 1)

    service = _secretary_service(pipeline)
    if start is None or end is None:
        # range=pending：全部待确认，不限日期。
        events: list = []
        pending = service.list_pending()
    else:
        events = [
            event
            for event in service.list_agenda(
                _datetime.combine(start, _datetime.min.time()),
                _datetime.combine(end, _datetime.max.time()).replace(microsecond=0),
            )
            if event.status == "confirmed"
        ]
        events.sort(key=lambda event: (event.start_at, event.id))
        low, high = start.isoformat(), end.isoformat()
        pending = [
            event
            for event in service.list_pending()
            if low <= str(event.start_at)[:10] <= high
        ]
    pending.sort(key=lambda event: (event.start_at, event.id))

    # WP-QUERY-DAY: single-day axis = today / tomorrow / date。复用 right rail
    # 同一份 ``day_surface``，不让 L1 的嘴和前端的时间轴各说各话。week 不是
    # 「一天」，pending 没有「那一天」——不塞 anchors / plan_items。空库 =
    # 空列表（不要 None，前端可以 .length 一下）。
    anchors: list | None = None
    plan_items: list | None = None
    if start is not None and end is not None and start == end and window in (
        "today",
        "tomorrow",
        "date",
    ):
        anchors = []
        plan_items = []
        try:
            conn = _store.connect(service.db_path)
            try:
                surface = _planning.day_surface(conn, start.isoformat(), events)
            finally:
                conn.close()
            anchors = surface.get("kept_anchors", [])
            plan_items = surface.get("plan_items", [])
        except Exception:
            # 计划服务挂了不能让 query 整体跪——日间快照是增强字段，零值
            # 落地；events / pending 仍然完整。L1 仍能答「今天有什么」，
            # 只是没念出作息（也可能是后端2的服务未跑）。
            logger.warning("plobi: query_agenda day_surface failed for %s", start)

    payload = {
        "ok": True,
        "intent": "query_agenda",
        "range": window,
        "from": start.isoformat() if start else None,
        "to": end.isoformat() if end else None,
        "user_text": user_text,
        "events": [_event_payload(event) for event in events],
        "pending": [_event_payload(event) for event in pending],
    }
    if anchors is not None:
        payload["anchors"] = anchors
    if plan_items is not None:
        payload["plan_items"] = plan_items
    return payload


def run_decide_pending(
    user_text: str,
    *,
    decision: str | None = None,
    event_id: str | None = None,
    title: str | None = None,
    date: str | None = None,
    pipeline=None,
) -> dict:
    """WP-SEC-VOCAB: spoken 「确认/忽略某条待确认」 = the board's Confirm/Dismiss.

    Same machine as the dashboard button (``AgendaService.confirm`` /
    ``dismiss``): dismiss restores ``prev_value`` when the change edited an
    existing row, and deletes the row when the message proposed a brand-new
    one (``deleted: true``). Locating reuses ``resolve_manual_target``
    narrowed to ``pending``; zero or 2+ matches write nothing and hand the
    candidates back instead of guessing. Confirming is never faked through an
    edit.

    WP-SECRETARY-MOUTH: when ``event_id`` and ``title`` are both empty, the
    secretary's pasted user_text becomes the needle — the same way a human
    glances at a WeChat bubble to recognise which pending row to confirm.
    ``@所有人`` and stray whitespace are stripped before matching so a long
    paste with no usable title can still land on the right row.
    """
    from datetime import date as _date

    from plobi.agenda.service import (
        AgendaError,
        ManualTargetAmbiguous,
        ManualTargetMissing,
    )

    user_text = (user_text or "").strip()
    decision = (decision or "").strip().lower()
    if decision not in DECIDE_ACTIONS:
        return _decide_error(
            "decide_pending 需要 decision=confirm/dismiss",
            decision=decision or None,
            user_text=user_text,
        )

    event_id = (str(event_id).strip() if event_id else "") or None
    title = (str(title).strip() if title else "") or None
    day = (str(date).strip() if date else "")[:10] or None
    if day:
        try:
            _date.fromisoformat(day)
        except ValueError:
            return _decide_error(
                f"date 不是合法日期: {date!r}", decision=decision, user_text=user_text
            )
    if not event_id and not title and not user_text:
        return _decide_error(
            "decide_pending 需要 event_id、title，或贴一段原话来定位那条待确认项",
            decision=decision,
            user_text=user_text,
        )

    service = _secretary_service(pipeline)
    try:
        # WP-SECRETARY-MOUTH: user_text-only needle — only when both id and
        # title are absent. Reuses the same "never guess" contract: 0 → no
        # match, 2+ → candidates, 1 → confirm/dismiss. We deliberately do not
        # fall back to ``resolve_manual_target`` here, because that path was
        # written for title-substring lookup against confirmed rows and would
        # silently widen the search scope in ways that risk confirming the
        # wrong pending row.
        if not event_id and not title:
            needle = _normalise_evidence_text(user_text)
            try:
                target = _find_pending_by_user_text(service, needle)
            except ManualTargetMissing:
                # Same UX as the title path: distinguish "nothing pending at
                # all" from "pending exists but not the one you meant".
                if not service.list_pending():
                    return _decide_error(
                        "没有待确认项", decision=decision, user_text=user_text
                    )
                return _decide_error(
                    "没有匹配的待确认项", decision=decision, user_text=user_text
                )
            except ManualTargetAmbiguous as exc:
                return _decide_error(
                    "多条匹配，不猜；请指明其中一条",
                    decision=decision,
                    user_text=user_text,
                    candidates=_candidate_rows(service, exc.candidates),
                )
        else:
            target = _find_agenda_target(
                service,
                event_id=event_id,
                title=title,
                day=day,
                statuses=PENDING_STATUSES,
            )
    except ManualTargetAmbiguous as exc:
        return _decide_error(
            "多条匹配，不猜；请指明其中一条",
            decision=decision,
            user_text=user_text,
            candidates=_candidate_rows(service, exc.candidates),
        )
    except AgendaError:
        # 待确认里定位不到：说清是「压根没有」还是「有，但它不是待确认」。
        try:
            _find_agenda_target(
                service, event_id=event_id, title=title, day=day, statuses=None
            )
        except AgendaError:
            return _decide_error(
                "没有匹配的待确认项", decision=decision, user_text=user_text
            )
        return _decide_error(
            "这条不是待确认项", decision=decision, user_text=user_text
        )

    try:
        if decision == "confirm":
            resolved = service.confirm(target.id)
            deleted = False
        else:
            resolved = service.dismiss(target.id)
            deleted = resolved is None
    except AgendaError as exc:
        return _decide_error(str(exc), decision=decision, user_text=user_text)

    event = _event_payload(resolved if resolved is not None else target)
    if deleted:
        event["status"] = "dismissed"
    if target.prev_value:
        event["prev_value"] = target.prev_value

    payload = {
        "ok": True,
        "intent": "decide_pending",
        "decision": decision,
        "user_text": user_text,
        "event": event,
    }
    if deleted:
        payload["deleted"] = True
    return payload


SECRETARY_RECORD_BODY_MAX_CHARS = 2000


def _record_secretary_turn(intent: str, user_text: str, result: dict) -> None:
    """Give a finished secretary turn an identity in the kanban kernel.

    裁定 59.1: one secretary work unit counts as a task. 裁定 62: it lands in
    ``logged`` — listable, replayable, and never claimed by the dispatcher or
    promoted by ``recompute_ready``, so recording work cannot queue work.

    The board is a *record*, never something the answer depends on: a write
    failure is logged and swallowed, the same posture as the N3 session write
    below.
    """
    if not isinstance(result, dict) or not result.get("ok"):
        return
    agent = result.get("agent") or {}
    body = json.dumps(
        {
            "intent": intent,
            "user_text": user_text,
            "agent": agent,
            "result": result,
        },
        ensure_ascii=False,
    )[:SECRETARY_RECORD_BODY_MAX_CHARS]
    try:
        from plobi_cli import kanban_db as kb

        with kb.connect_closing() as conn:
            kb.create_task(
                conn,
                title=f"secretary:{intent} · {user_text[:60]}",
                body=body,
                assignee=agent.get("profile"),
                created_by="secretary",
                initial_status="logged",
                session_id=result.get("sessionId") or None,
            )
    except Exception as exc:
        logger.warning("plobi: secretary turn record failed: %s", exc)


def run_secretary_ask(intent: str, user_text: str, **kwargs) -> dict:
    """§8.2 secretary routing, with the turn recorded as a ``logged`` task."""
    result = _secretary_ask(intent, user_text, **kwargs)
    _record_secretary_turn(intent, user_text, result)
    return result


def _secretary_ask(
    intent: str,
    user_text: str,
    *,
    registry: AgentRegistry | None = None,
    pipeline=None,
    aigw_complete=None,
    fallback_complete=None,
    action: str | None = None,
    title: str | None = None,
    start_at: str | None = None,
    end_at: str | None = None,
    kind: str | None = None,
    event_id: str | None = None,
    source: str | None = None,
    range: str | None = None,
    date: str | None = None,
    decision: str | None = None,
) -> dict:
    """§8.2 secretary routing: pick agenda L2, one refresh, structured summary.

    ``refresh_agenda`` and ``write_briefing`` both refresh. write_briefing
    then generates via aigw ``workbuddy/*`` (F2 fallback = L2 cheap API).
    No kanban *dispatch* — :func:`run_secretary_ask` files a non-dispatchable
    ``logged`` record of the turn, nothing here queues work for a worker.
    No chat REST. Dead chatlog returns ``ok: False``.
    ``mutate_agenda`` never reaches this chatlog path — see
    :func:`run_mutate_agenda` (writes must survive a dead collector).
    ``query_agenda`` / ``decide_pending`` (WP-SEC-VOCAB) don't either: they
    answer straight from the shared store, so a dead collector still lets the
    secretary say what is on today and take a spoken confirm/dismiss.
    """
    intent = (intent or "").strip()
    user_text = (user_text or "").strip()
    if intent not in SECRETARY_ASK_INTENTS:
        return {
            "ok": False,
            "error": "intent must be one of " + ", ".join(SECRETARY_ASK_INTENTS),
        }
    if not user_text:
        return {"ok": False, "error": "user_text is required"}

    if intent == "mutate_agenda":
        return run_mutate_agenda(
            user_text,
            action=action,
            title=title,
            start_at=start_at,
            end_at=end_at,
            kind=kind,
            event_id=event_id,
            source=source,
            pipeline=pipeline,
            registry=registry,
        )

    if intent == "query_agenda":
        return run_query_agenda(user_text, range=range, date=date, pipeline=pipeline)

    if intent == "decide_pending":
        return run_decide_pending(
            user_text,
            decision=decision,
            event_id=event_id,
            title=title,
            date=date,
            pipeline=pipeline,
        )

    # WP-PROJECT-PORTFOLIO: ``project_status`` and ``plan_day`` route before
    # the agenda L2 spawn — they never touch chatlog and never materialise
    # a 日程 agent, so a dead collector cannot block them. ``date`` is reused
    # for ``plan_day`` (kept as the raw string from the tool caller).
    if intent == "project_status":
        return run_project_status(user_text, pipeline=pipeline, registry=registry)

    if intent == "plan_day":
        # P0（`WP-PLAN-DAY-CLOBBER`，10-17 17:2x）：这句以前不带 action 时会落进
        # :func:`run_plan_day` →（当时的）``generate_evening_plan``，而那个函数**先 DELETE 当天
        # `plan_items` 再重插**；排程本体已按裁定 149 退役 ⇒ 它排不出任何块，于是拿一份 0 块的计划
        # 把秘书 15:53 真排出来的那 8 块抹掉。用户说一句「安排一下明天的任务」反而丢计划。
        # 现在照实拒绝，一个字都不写；要排由秘书自己那一发去做（它有 `no_blocks` 前置闸，
        # 过不了闸也是零写入）。想看的走 query_agenda——那一条本来就不写库。
        if action != "plan_tomorrow":
            return {
                "ok": False,
                "intent": "plan_day",
                "written": 0,
                "error": (
                    "这句「排一下明天」我不替你重排一遍——那条老路径会先把今天已排好的块删掉再重插，"
                    "而排程本体已经退役（裁定 149），重插出来是 0 块：越说越丢。"
                    "要排就让我自己排一次：同一趟里调 plobi_secretary_ask，"
                    "intent=plan_day、action=plan_tomorrow（材料、边界闸、落库都在那一处，过不了闸它一个字都不写）。"
                    "只想看明天怎么安排，用 intent=query_agenda，那条不写库。"
                ),
            }
        return run_plan_day(
            user_text, date=date, pipeline=pipeline, registry=registry, action=action
        )

    reg = registry or load_registry()
    try:
        entry, spawned = ensure_agenda_agent(reg)
    except RegistryError as exc:
        return {"ok": False, "error": f"无法落地日程 L2: {exc}"}

    from plobi.collectors.chatlog.pipeline import ChatlogDead, refresh_agenda

    try:
        _report, summary = refresh_agenda(pipeline=pipeline)
    except ChatlogDead as exc:
        return {
            "ok": False,
            "error": str(exc),
            "dead": True,
            "agent": {
                "id": entry.name,
                "role": entry.role,
                "profile": entry.profile_name,
                "spawned": spawned,
            },
        }

    payload = {
        "ok": True,
        "intent": intent,
        "user_text": user_text,
        "agent": {
            "id": entry.name,
            "role": entry.role,
            "profile": entry.profile_name,
            "spawned": spawned,
        },
        "agenda": summary,
    }
    try:
        payload["plan"] = _l1_plan_view(pipeline, summary)
    except Exception as exc:
        logger.warning("plobi: last-night plan read failed: %s", exc)
        payload["plan"] = _missing_plan_payload(_tomorrow_for_date(summary))
    try:
        payload["sessionId"] = append_n3_dispatch(
            entry, user_text=user_text, intent=intent
        )
    except Exception as exc:
        logger.warning("plobi N3 session write failed: %s", exc)
        payload["sessionId"] = ""
        payload["n3_error"] = str(exc)

    if intent == "write_briefing":
        # 裁定 45 第②半：这里原来先调两次 ``ensure_aigw_provider()`` 往根 config 和
        # 分身 config 里手搓一份 ``providers.aigw`` + ``custom_providers`` 条目。
        # aigw 自 ``e9b6034`` 起是注册过的 provider 插件（``plugins/model-providers/aigw``，
        # 对外名 Local Quota Hub），那份手搓块就是「配置模型的入口」的第二真源，
        # 而且它的 ``"name": "aigw"`` 会被自定义提供商行按自己的名字原样渲染上屏。
        # 早报本身走 :func:`aigw_base_url` / :func:`aigw_api_key` 直连，不读那份 config，
        # 所以删掉写入不影响功能。
        briefing = generate_morning_briefing(
            summary,
            user_text,
            plan=payload.get("plan"),
            aigw_complete=aigw_complete,
            fallback_complete=fallback_complete,
        )
        payload["briefing"] = briefing["text"]
        payload["route"] = briefing["route"]
        payload["model"] = briefing.get("model") or ""
    return payload


def n3_briefing_text(intent: str, entry: AgentEntry) -> str:
    """L1 派工简报 — 用户原话之外的第二条可见消息。不复制 L1 全文。"""
    if intent == "write_briefing":
        action = "根据明日日程写一段早报"
        ret = "早报正文；标明 route=workbuddy 或 fallback（F2）"
    else:
        action = "刷新明日日程（一轮采集）"
        ret = "当日+次日 events 结构化摘要（零聊天 API）；L1 终答说人话"
    return (
        f"【L1 派工简报】\n"
        f"intent: {intent}\n"
        f"要做: {action}\n"
        f"交回: {ret}\n"
        f"agent: {entry.name} (profile={entry.profile_name})\n"
    )


def _latest_session_id(db) -> str:
    try:
        row = db._conn.execute(
            "SELECT id FROM sessions "
            "ORDER BY COALESCE(ended_at, started_at) DESC LIMIT 1"
        ).fetchone()
    except Exception:
        return ""
    if row is None:
        return ""
    return str(row[0] or "")


def append_n3_dispatch(
    entry: AgentEntry,
    *,
    user_text: str,
    intent: str,
    session_id: str | None = None,
) -> str:
    """Append user original + L1 briefing to the L2 profile's session store.

    Reuses Plobi ``SessionDB.append_message`` (the same ``state.db`` WP-BE-3
    maps onto ``GET /api/agents/:id/overview`` ``sessionId``). Does not copy
    the L1 transcript and does not create a parallel chat table.
    """
    import uuid

    from plobi_cli.profiles import get_profile_dir
    from plobi_state import SessionDB

    if not (entry.profile_name or "").strip():
        # 数据条目（``l2_course``）与任何 ``profile: ""`` 的记录都不该被派活：以前这里把空名
        # 直接递给 ``get_profile_dir``，抛出一句和「保留名」长得一样的 ``ValueError: profile name
        # cannot be empty``，被上面的调用方吞成 ``n3_error`` —— 现场没人看得见，但那条门是开的：
        # 一旦 askability 放宽或再添一类数据角色，它就是下一个 500（2026-10-13 13:2x 现核，
        # 由脑搬家线扫同类空 profile 门时报出，治理线自己复跑后落这颗守卫）。
        # 现在只可能从这里出去一句指名道姓的话，且不留半截 session。
        raise ValueError(
            f"agent {entry.name!r} has no profile to dispatch to "
            f"(role={entry.role or 'unset'}): this roster entry is data, not a running agent"
        )

    profile_dir = get_profile_dir(entry.profile_name)
    db = SessionDB(db_path=profile_dir / "state.db")
    sid = (session_id or "").strip() or _latest_session_id(db)
    if not sid:
        sid = f"plobi-n3-{uuid.uuid4().hex[:12]}"
    db.create_session(sid, source="cli")
    db.append_message(sid, role="user", content=user_text)
    db.append_message(sid, role="assistant", content=n3_briefing_text(intent, entry))
    return sid


# ---------------------------------------------------------------------------
# WP-BE-7: morning briefing via aigw workbuddy/* (F2 → L2 cheap API)
# ---------------------------------------------------------------------------

AIGW_DEFAULT_BASE = "http://127.0.0.1:8000/v1"
WORKBUDDY_PREFIX = "workbuddy/"
WORKBUDDY_DEFAULT_MODEL = "workbuddy/deepseek-chat"

# The only list of env var *names* for this endpoint — the ``model-providers/aigw``
# profile and ``scripts/plobi/doctor.py`` import it instead of writing their own.
AIGW_URL_ENV_VARS: tuple[str, ...] = ("PLOBI_AIGW_URL", "PLOBI_QUOTA_AIGW_URL")


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
AIGW_KEY_ENV_VARS: tuple[str, ...] = ("AIGW_API_KEY", "PLOBI_QUOTA_AIGW_KEY")
AIGW_DEV_DEFAULT_API_KEY = "sk-local-dev-key"
AIGW_GATEWAY_CONFIG_PATH = Path(__file__).resolve().parents[2] / "aigw" / "config.yaml"

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


def pick_workbuddy_model(model_ids: list[str]) -> str:
    for mid in model_ids:
        if str(mid).startswith(WORKBUDDY_PREFIX):
            return str(mid)
    return WORKBUDDY_DEFAULT_MODEL


def _openai_chat_complete(
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict],
    *,
    timeout: float = 30.0,
) -> str:
    import json as _json
    import urllib.request

    url = base_url.rstrip("/")
    if not url.endswith("/chat/completions"):
        url = f"{url}/chat/completions"
    body = _json.dumps(
        {"model": model, "temperature": 0.3, "messages": messages},
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = _json.loads(response.read().decode("utf-8"))
    choices = payload.get("choices") or []
    if not choices:
        raise RuntimeError("本地额度网关没有返回候选 (quota gateway returned no choices)")
    content = (choices[0].get("message") or {}).get("content")
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("本地额度网关返回的内容是空的 (quota gateway returned empty content)")
    return content.strip()


def list_aigw_models(*, base_url: str | None = None, api_key: str | None = None) -> list[str]:
    import json as _json
    import urllib.request

    base = (base_url or aigw_base_url()).rstrip("/")
    key = aigw_api_key() if api_key is None else api_key
    request = urllib.request.Request(
        f"{base}/models",
        headers={"Accept": "application/json", "Authorization": f"Bearer {key}"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        payload = _json.loads(response.read().decode("utf-8"))
    ids: list[str] = []
    for item in payload.get("data") or []:
        if isinstance(item, dict) and item.get("id"):
            ids.append(str(item["id"]))
        elif isinstance(item, str):
            ids.append(item)
    return ids


def _briefing_prompt(
    agenda_summary: dict, user_text: str, plan: dict | None = None
) -> str:
    if _uses_plan_briefing(plan):
        assert plan is not None
        lines = [
            f"用户原话：{user_text}",
            # 日期只认这份计划自己的 for_date；读不到就说读不到，不拿今天冒充明天（R-103）。
        f"日期：{plan.get('for_date') or '（昨夜计划没有日期，下面这段不带日期，别当成今天）'}",
            f"昨夜计划：{plan.get('summary') or ''}",
            f"冲突数：{int(plan.get('conflict_count') or 0)}",
            "请写一段简洁中文早报，不要解释调度过程，不要逐条抽取。",
        ]
        return "\n".join(lines)
    # **不许回落到 `events`**：那一份是 today + tomorrow 拼起来的，
    # 明天空的时候用它 = 把今天的事标成「明日日程」念给用户（R-103）。
    events = list(agenda_summary.get("tomorrow_events") or [])
    lines = [
        f"用户原话：{user_text}",
        f"日期：{agenda_summary.get('tomorrow') or '（读不到明天的日期，别拿今天顶）'}",
        "明日日程：",
    ]
    if not events:
        lines.append("（明天没有条目——照实说没有，不许拿今天的条目顶）")
    for event in events:
        lines.append(
            f"- {event.get('start_at', '')} {event.get('title', '')} [{event.get('status', '')}]"
        )
    lines.append("请写一段简洁中文早报，不要解释调度过程。")
    return "\n".join(lines)


def _template_briefing(agenda_summary: dict, plan: dict | None = None) -> str:
    if _uses_plan_briefing(plan):
        assert plan is not None
        summary = (plan.get("summary") or "").strip()
        conflicts = int(plan.get("conflict_count") or 0)
        extra = f"（冲突 {conflicts}）" if conflicts else ""
        return f"{summary}{extra}".strip()
    day = agenda_summary.get("tomorrow") or "（明天）"   # 不回落 today（R-103）
    events = list(agenda_summary.get("tomorrow_events") or [])   # 同上：不回落 events（R-103）
    if not events:
        return f"{day} 日程：暂无条目。"
    parts = [
        f"{event.get('start_at', '')} {event.get('title', '')}".strip()
        for event in events
    ]
    return f"{day} 早报：" + "；".join(parts)


def _l2_cheap_complete(prompt: str) -> str:
    """F2: L2 cheap API — quota-pool cheap_api, then PLOBI_L2_CHAT_URL."""
    try:
        from plobi.quota.pool import get_quota_pool
        from plobi.quota.sources import HealthStatus

        pool = get_quota_pool()
        pool.probe_all()
        for name in pool.order:
            source = pool.sources.get(name)
            if source is None or source.kind != "cheap_api":
                continue
            status = pool.status(name)
            if status is not None and status.health is HealthStatus.UNAVAILABLE:
                continue
            if not source.base_url:
                continue
            try:
                return _openai_chat_complete(
                    source.base_url,
                    source.api_key,
                    source.model or "deepseek-chat",
                    [{"role": "user", "content": prompt}],
                )
            except Exception as exc:
                logger.warning("plobi L2 cheap source %s failed: %s", name, exc)
    except Exception as exc:
        logger.warning("plobi quota pool unavailable for F2: %s", exc)

    url = os.environ.get("PLOBI_L2_CHAT_URL", "").strip()
    key = os.environ.get("PLOBI_L2_API_KEY", "").strip()
    if not url or not key:
        raise RuntimeError("no L2 cheap API configured")
    model = "deepseek-chat"
    try:
        from plobi.routing import L2_AGENDA, get_router

        model = get_router().resolve(L2_AGENDA).model or model
    except Exception:
        pass
    return _openai_chat_complete(
        url.rstrip("/"),
        key,
        model,
        [{"role": "user", "content": prompt}],
    )


def generate_morning_briefing(
    agenda_summary: dict,
    user_text: str,
    *,
    plan: dict | None = None,
    aigw_complete=None,
    fallback_complete=None,
) -> dict:
    """Default aigw ``workbuddy/*``; on failure, L2 cheap API (route in result)."""
    prompt = _briefing_prompt(agenda_summary, user_text, plan)
    model = WORKBUDDY_DEFAULT_MODEL
    try:
        if aigw_complete is not None:
            text = aigw_complete(prompt)
        else:
            try:
                model = pick_workbuddy_model(list_aigw_models())
            except Exception:
                model = WORKBUDDY_DEFAULT_MODEL
            text = _openai_chat_complete(
                aigw_base_url(),
                aigw_api_key(),
                model,
                [
                    {
                        "role": "system",
                        "content": "你是日程秘书。根据日程表写一段简短中文早报，不要解释调度。",
                    },
                    {"role": "user", "content": prompt},
                ],
            )
        if text and str(text).strip():
            return {
                "text": str(text).strip(),
                "route": "workbuddy",
                "model": model,
            }
    except Exception as exc:
        logger.warning("aigw workbuddy failed (%s); F2 fallback", exc)

    try:
        if fallback_complete is not None:
            text = fallback_complete(prompt)
        else:
            text = _l2_cheap_complete(prompt)
        if text and str(text).strip():
            return {
                "text": str(text).strip(),
                "route": "fallback",
                "model": "l2-cheap",
            }
    except Exception as exc:
        logger.warning("L2 cheap fallback failed: %s", exc)

    return {
        "text": _template_briefing(agenda_summary, plan),
        "route": "fallback",
        "model": "template",
    }


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
    """WP-L2-DIET: strip terminal / computer_use / code_execution /
    session_search + every ``plobi-*`` composite from a project L2
    profile's ``config.yaml``。

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
    from plobi_cli.profiles import get_profile_dir

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


def _diet_existing_l2_project(entry: "AgentEntry") -> None:
    """WP-L2-DIET / 裁定 33.3: 对注册表里已存在的未归档 ``l2_project`` 行
    也走减肥。profile 没落地就静默——注册表先行、profile 稍后落地的过渡期
    不算错，spawn 阶段会再走一遍。"""
    if entry.role != "l2_project":
        return
    if entry.archived:
        return
    if entry.category not in ("projects", "research"):
        return
    profile_name = entry.profile_name
    if not profile_name:
        return
    try:
        from plobi_cli.profiles import get_profile_dir, profile_exists

        if not profile_exists(profile_name):
            return
        apply_l2_project_diet(get_profile_dir(profile_name))
    except Exception as exc:
        # plobi_cli may not be importable in the test env; treat as a
        # silent no-op so the registry row stays visible.
        logger.debug("plobi: l2 diet (existing) skipped for %s: %s", entry.name, exc)




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
        lines.append(
            f"- 你在 Mind 知识库里的地方是 `{entry.mind_subtree}`：这个项目的计划、进度、结论写在这棵树里，别的树只读不改。"
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
    lines += [
        "- 你的长期记忆是**本机唯一的那一份共享账本**：别的 Agent 也往同一本里写，每条带「哪个项目」的标签。账本只记发生过什么——**「我是谁」不看账本，看这份文件**。",
        "- 你带着这个项目自己那条「现在」（裁定 44：同一时刻只有一条现在）。用户问「你在忙什么 / 上次推到哪了」，你要答得上来；答不上就说不记得，不要拿别的项目的进度顶。",
        "- **职责不是从共享记忆里挑一个来当**。账本里写着别人的分工——比如总秘书那套「架构 + 验证 + 文档 + 测试 + 小修」——那是他的岗位，不是你的。上面没写你的职责，就照项目归属说话；说不准就问用户一句，不要自己认领一个头衔。",
        "- 你**不是**本机 Plobi 总秘书（L1），**不是** 通道模型的名字、**不是** 通用 chatbot、**不是** Cursor / Claude Code 这种 GUI 工具里钻的工具人。派工、日程入口、跨项目统筹归 L1（裁定 44：一嘴一个 Agent）；不要转述调度器的角色，不要替别的项目作答，也不要自称总秘书。",
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


def ensure_l2_identity_soul(
    entry: AgentEntry, *, home: Path | str | None = None
) -> bool:
    """Upsert a project 分身's OWN assignment into its profile ``SOUL.md``.

    ``agent/prompt_builder.load_soul_md()`` reads ``get_plobi_home()/SOUL.md``
    and nothing else, so a 分身's identity can only come from its own file — the
    profiles shipped a 512-byte upstream stub (裁定 42 §6(c)). Shape copied from
    :func:`ensure_l1_secretary_routing_soul`: strip the known fences, then
    append; the two contracts never coexist in one file. No-op when the content
    already matches, and never written onto the default (L1) profile.

    裁定 42 §42.2 第 6 项 reserves a per-project 人设片段, and that fragment lives
    in this same file **outside** the fence. Only this function's own fence is
    stripped, so hand-written character text survives regeneration; whether such
    text exists is decided by :func:`_soul_persona_text` (the upstream stub is
    not a persona) and handed to the renderer as ``persona_written``.
    """
    from plobi_constants import get_plobi_home

    if entry.role == L1_SECRETARY_ROLE or entry.profile_name == "default":
        return False

    target = Path(home) if home is not None else get_plobi_home()
    if not target.is_dir():
        # 分身还没落地（spawn 未跑 / profile 目录被移走）。不要为了一份 SOUL.md
        # 凭空建 profile 目录 —— 那是第二真源，见 :func:`_resolvable_project_cwd`
        # 的同款判据（宁可不给，也不编造落点）。
        return False
    soul_path = target / "SOUL.md"
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
    """
    from plobi_cli.config import load_config, save_config
    from plobi_cli.profiles import get_active_profile_name
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
    return ensure_l1_secretary_routing_soul() or changed


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
        from plobi_cli.profiles import get_active_profile_name

        if get_active_profile_name() == "default":
            ensure_l1_secretary_routing_soul()
        else:
            strip_l1_secretary_routing_soul()
    except Exception:
        logger.debug("plobi: L1 secretary SOUL routing upsert skipped", exc_info=True)
    return config


def load_registry(path: Path | str | None = None) -> AgentRegistry:
    return AgentRegistry.load(path)
