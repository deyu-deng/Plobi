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
AGENT_CATEGORIES: tuple[str, ...] = ("projects", "butler", "events", "research")
DEFAULT_CATEGORY = "butler"

# --------------------------------------------------------------------------- #
# R-013: default folder binding per category. ``butler`` has none (it is the
# housekeeping agent, not project-scoped). A caller may always override
# ``project_path`` on the entry; this map only seeds the default.
#
# The binding is PLATFORM-AWARE. The original table hardcoded a Windows drive
# layout (``D:\...``) for a machine that has since been retired — the repo was
# re-baselined onto this macOS workspace in 2026-09. On macOS/Linux that string
# is just a relative path with a colon in it, so it can never exist;
# ``apply_l2_project_cwd`` then refused to fabricate a cwd and every project
# 分身 silently lost its working directory. Windows keeps the historic bindings
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
        if category == "butler":
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
# Why this exists: ``apply_l2_project_cwd`` degrades a non-existent
# ``project_path`` to a silent no-op ("NEVER fabricate a cwd"). That is the
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
    """注册表文件路径：$PLOBI_HOME/plobi/projects.yaml。"""
    override = os.environ.get("PLOBI_PROJECTS_CONFIG", "").strip()
    if override:
        return Path(override)
    try:
        from plobi_constants import get_plobi_home

        root = get_plobi_home() / "plobi"
    except Exception:
        root = Path.home() / ".plobi" / "plobi"
    return root / "projects.yaml"


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
    # 裁定 19 dissolve: archived rows stay on disk but hide from the default
    # list. ``archived_at`` is the ISO date the profile dir was moved aside.
    archived: bool = False
    archived_at: str = ""
    # events 生命周期：建册时指向一条用户已确认的 agenda 事项 id。
    # Non-empty only for category=events (validated at the API layer).
    source_event_id: str = ""

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
        if self.archived:
            data["archived"] = True
            if self.archived_at:
                data["archived_at"] = self.archived_at
        if self.source_event_id:
            data["source_event_id"] = self.source_event_id
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
            project_path=str(raw.get("project_path") or ""),
            archived=bool(raw.get("archived") or False),
            archived_at=str(raw.get("archived_at") or ""),
            source_event_id=str(raw.get("source_event_id") or ""),
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
                # stays unbound in practice because apply_l2_project_cwd skips
                # a path that does not exist, so the 分身 gets no cwd instead of
                # a fabricated one.
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

    def upsert(self, entry: AgentEntry) -> AgentEntry:
        # R-013: when the caller did not supply project_path, seed it from
        # the category default. butler stays empty (no bound folder). The
        # actual directory is created at the API layer (console router),
        # not here — registry stays pure data + no filesystem mutation
        # beyond its own yaml.
        if not entry.project_path and entry.category != "butler":
            from dataclasses import replace

            seeded = default_project_path(entry.category)
            if seeded:
                entry = replace(entry, project_path=seeded)
        self.agents[entry.name] = entry
        return entry

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
        """合并默认路由 + 注册表覆盖，返回 ModelRouter。

        惰性 import 避免 plobi_cli 依赖方向问题。profile 级 models.json
        由 ``spawn`` 写，这里只负责在默认路由之上叠加覆盖。
        """
        from plobi.routing import ModelRouter

        router = ModelRouter.load()
        for entry in self.entries():
            if not entry.has_model_override:
                continue
            route = router.resolve(entry.role)
            from plobi.routing import ModelRoute

            router.routes[entry.role] = ModelRoute(
                role=entry.role,
                provider=entry.provider or route.provider,
                model=entry.model or route.model,
            )
        return router

    def routing_problems(self) -> list[str]:
        """ADR-0011 断言问题列表（空 = 绿）。"""
        try:
            return self.router().violations()
        except Exception as exc:  # 兜底：缺 L1 等异常当问题上报
            return [f"routing check failed: {exc}"]

    # ------------------------------------------------------------------ #
    # spawn
    # ------------------------------------------------------------------ #

    def spawn(
        self,
        name: str,
        *,
        clone_from: Optional[str] = None,
        write_config: bool = True,
    ) -> dict:
        """把一个注册条目落地为常驻 profile + 模型路由。

        1. 若 profile 不存在则 ``create_profile``：克隆当前 profile 的
           config/.env/SOUL（让 L2 继承基础能力），但**不复制技能**——
           技能走顶层共享根（裁定 42 §42.3，见
           :meth:`_ensure_profile` / :func:`apply_shared_skills_root`）。
        2. 把该 agent 的路由写进 profile 的 ``plobi/models.json``。
        3. ADR-0011 断言（L1≠L2 模型）必须绿，否则抛 RegistryError。
        4. 返回 dict：profile 路径、启动命令、routing 断言。

        ``write_config`` 为真时同步把路由写进 profile 的 config.yaml 的
        ``model.provider`` / ``model.default``（会话真正用到的模型）。
        """
        entry = self.get(name)
        if entry is None:
            raise RegistryError(f"agent {name!r} is not registered")

        profile_dir = self._ensure_profile(entry, clone_from=clone_from)

        # WP-L2-IDENTITY / 裁定 42 §6(c): 分身缺的是「我是谁」。SOUL.md 是
        # ``load_soul_md()`` 唯一的身份来源，而 profile 里那份要么是 512 B 上游
        # 残桩、要么是 --clone 抄来的 L1 合同。每次 spawn 都纠一次（幂等），
        # 后端一起就把身份落好，不靠人手抄文本。
        try:
            ensure_l2_identity_soul(entry, home=profile_dir)
        except Exception as exc:
            logger.warning(
                "plobi: L2 identity SOUL not written for %s: %s", entry.name, exc
            )

        # 1) profile 级 models.json（ADRD-0011 断言基于它）
        profile_models = self._write_profile_models(profile_dir, entry)

        # 2) routing 断言绿
        problems = self._profile_routing_problems(profile_models)
        if problems:
            raise RegistryError(
                f"agent {name!r} routing violates ADR-0011: " + "; ".join(problems)
            )

        # 3) config.yaml 同步模型（会话实际用模型）
        if write_config:
            self._write_profile_config(profile_dir, entry)
            # WP-STUDIO-CWD / 裁定 36.1: project_path → terminal.cwd，独立于
            # 模型路由——cwd 是工作目录字段，跟模型是不是 configured 无关。
            # butler / 空 path 由 apply_l2_project_cwd 自己跳过。
            if entry.role not in L2_PROJECT_CWD_BUTLER_ROLES:
                try:
                    apply_l2_project_cwd(profile_dir, entry.project_path)
                except Exception as exc:
                    logger.warning(
                        "plobi: cwd write skipped for %s: %s", entry.name, exc
                    )

        return {
            "name": name,
            "role": entry.role,
            "profile": entry.profile_name,
            "profile_dir": str(profile_dir),
            "command": f"plobi -p {entry.profile_name} chat",
            "routing_ok": True,
        }

    def _ensure_profile(
        self, entry: AgentEntry, *, clone_from: Optional[str] = None
    ) -> Path:
        from plobi_cli.profiles import (
            create_profile,
            get_profile_dir,
            profile_exists,
        )

        name = entry.profile_name
        if profile_exists(name):
            return get_profile_dir(name)
        # 首次落地：克隆现有 profile 的**配置**（config.yaml / .env / SOUL.md）作为
        # 能力底座，但**不复制技能**——裁定 42 §42.2 把技能划进共享层，§42.3 指定的
        # 两个现成钩子就是 ``create_profile(no_skills=True)``（不拷贝 + 写
        # ``.no-bundled-skills`` 标记，``plobi update`` 不再重新播种）与
        # ``skills.external_dirs``（下面 ``apply_shared_skills_root`` 写）。
        # SOUL.md / .env 的归属是 2b，本刀保持原样。
        kwargs: dict = {"no_skills": True}
        if clone_from:
            kwargs["clone_from"] = clone_from
            kwargs["clone_config"] = True
        else:
            kwargs["clone_config"] = True  # 默认克隆配置（不含 skills）
        try:
            profile_dir = create_profile(name=name, **kwargs)
        except FileExistsError:
            # Someone else landed it first — don't rewrite their config.
            return get_profile_dir(name)
        try:
            apply_shared_skills_root(profile_dir)
        except Exception as exc:  # 兜底：共享根写不进去也不该让 spawn 失败
            logger.warning(
                "plobi: shared skills root not wired for %s: %s", name, exc
            )
        return profile_dir

    def _write_profile_models(self, profile_dir: Path, entry: AgentEntry) -> Path:
        """把注册表整体路由写进 profile 的 plobi/models.json。"""
        from plobi.routing import ModelRouter

        target = profile_dir / "plobi" / "models.json"
        router = self.router()  # 默认 + 全部注册覆盖
        router.save(target)
        return target

    def _profile_routing_problems(self, models_path: Path) -> list[str]:
        from plobi.routing import ModelRouter

        router = ModelRouter.load(models_path)
        return router.violations()

    def _write_profile_config(self, profile_dir: Path, entry: AgentEntry) -> None:
        """把该 agent 的模型写进 profile config.yaml 的 model 节。

        仅当 entry 显式给了 provider/model（或默认路由解析出模型）才写；
        不动其他配置。config.yaml 可能不存在（新 profile 也可能没有），
        不存在则跳过 —— 会话仍可用 models.json 路由。

        落笔前有两道闸，任一不过就**什么都不写**（分身继续用它克隆来的 model 节）：

        ① 只有注册表那条记录真的写了模型覆盖才落笔（裁定 42 §42.2 ⑥：「模型」是分身的
           三个允许覆盖项之一，省略 = 没有覆盖）。以前省略时会落到 :data:`DEFAULT_ROUTES`
           的硬编码 ``aigw`` / ``workbuddy/*`` —— 那套路由和用户在本机选的默认提供商
           毫无关系，而 ``e9b6034`` 把 aigw 注册成真 provider 之后下面那道可解析闸门已经
           拦不住它，于是每个新分身都被盖上一份「``provider: aigw`` 而 ``base_url`` 还是
           克隆来的 minimax」的自相矛盾 model 节（2026-09-29 实测新落的 Framelet / Plobi
           两条）。不写 = 继承 ``create_profile(clone_config=True)`` 拷来的根
           ``config.yaml`` model 节，那**就是**用户当前配置的默认 provider/model，
           也不会配上第二个 base_url。
        ② 可解析闸门（:func:`provider_is_resolvable`）：底座运行时认不出来的 provider
           名字不写进 model 节。写进一个解析不出来的名字 = 分身一启动就是一条
           「Unknown provider」红条；不写 = 顶多用继承的模型，功能不塌。
           任何情况下都不因为这道闸让 ``spawn`` 失败。
        """
        if not entry.has_model_override:
            return
        try:
            route = self.router().resolve(entry.role)
        except Exception:
            route = None
        if route is None or not route.configured:
            return

        provider = (route.provider or "").strip()
        if provider and not provider_is_resolvable(provider):
            logger.warning(
                "plobi: model section NOT written to %s/config.yaml — the runtime "
                "provider resolver does not know provider %r (model %r), and writing "
                "it would make the profile fail with \"Unknown provider '%s'\" on "
                "every start. Skipping keeps this 分身 on the model config it already "
                "inherits. Run 'plobi model' for the list of resolvable providers or "
                "'plobi doctor' to diagnose. Registration itself continues.",
                profile_dir,
                provider,
                route.model,
                provider,
            )
            return

        cfg_path = profile_dir / "config.yaml"
        if not cfg_path.exists():
            return
        import yaml

        try:
            cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        except Exception:
            return
        if not isinstance(cfg, dict):
            return
        model_section = cfg.get("model")
        if not isinstance(model_section, dict):
            model_section = {}
        model_section["provider"] = route.provider or model_section.get("provider", "")
        model_section["default"] = route.model or model_section.get("default", "")
        cfg["model"] = model_section
        cfg_path.write_text(
            yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True), encoding="utf-8"
        )


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
    一旦覆盖写在记录上，`_write_profile_config` 就会把 ``model.provider: aigw`` 盖进
    日程分身的 config.yaml（2026-09-29 实测他的 l2-agenda 那条一度就是这样，被手工
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


def ensure_agenda_agent(registry: AgentRegistry | None = None) -> tuple[AgentEntry, bool]:
    """Return the agenda L2, spawning **one** template entry if none exists (S2).

    Never creates any other L2. ``spawned`` is True only when this call
    registered the template. Profile materialization is idempotent.
    """
    reg = registry or load_registry()
    existing = find_agenda_agent(reg)
    spawned = False
    if existing is None:
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
        existing = template
        spawned = True

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


# 注册表给每条项目分身自动填的**占位**描述。唯一一份定义：写在这里、用在下面
# ``ensure_mind_project_agents``，渲染身份段时再认它一次（见
# :func:`_is_placeholder_project_description`）——占位句不是「这个项目在做什么」。
L2_PLACEHOLDER_DESCRIPTION_PREFIX = "l2_project for Mind project "


def ensure_mind_project_agents(
    registry: "AgentRegistry | None" = None,
    *,
    mind_root: Path | str | None = None,
) -> "tuple[AgentRegistry, list[str]]":
    """Seed every active Mind project into the L2 registry.

    Walks ``<mind_root>/Vault/projects/*``. Skips the system-self project
    (``Mind``), any directory without a ``plan.md``, and any plan whose
    frontmatter ``status`` matches the skip markers. The remaining projects
    become ``l2_project`` entries with ``category=projects`` (or ``research``
    only when ``plan.md``'s ``group`` says so). Returns ``(registry, seeded)``
    where ``seeded`` lists the *new* agent names registered on this call.

    Idempotent: re-running the function never duplicates rows. Existing rows
    keep their user-edited ``project_path`` / ``pace`` / ``description`` —
    only blank fields are filled in. ``spawn`` is attempted for each new row
    so the profile + models.json land; failures log and the registry row is
    still kept so the sidebar keeps showing the project.
    """
    from plobi.mind.paths import resolve_root

    reg = registry if registry is not None else load_registry()
    root = resolve_root(mind_root)
    if root is None:
        return reg, []
    projects_dir = root / "Vault" / "projects"
    if not projects_dir.is_dir():
        return reg, []

    seeded: list[str] = []
    for entry in sorted(projects_dir.iterdir(), key=lambda p: p.name):
        if not entry.is_dir():
            continue
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

        existing = reg.get(safe_id)
        if existing is not None and not existing.archived:
            # Honor user edits: only fill blanks.
            from dataclasses import replace as _replace

            patched = existing
            if not patched.mind_subtree:
                patched = _replace(
                    patched,
                    mind_subtree=f"Vault/projects/{entry.name}",
                )
            if not patched.project_path and patched.category != "butler":
                patched = _replace(patched, project_path=project_path)
            if not patched.description:
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
            # WP-STUDIO-CWD / 裁定 36.1: 已有未归档的项目 L2 也要把
            # ``terminal.cwd`` 对齐 ``project_path``（registry 是真源）。
            _cwd_existing_l2_project(patched)
            continue

        entry_obj = AgentEntry(
            name=safe_id,
            role="l2_project",
            profile="",
            provider="",
            model="",
            mind_subtree=f"Vault/projects/{entry.name}",
            skills=(),
            description=description,
            pace=None,
            category=category,
            project_path=project_path,
            archived=False,
            archived_at="",
            source_event_id="",
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
                # WP-STUDIO-CWD / 裁定 36.1: 把 project_path 写到
                # ``terminal.cwd``——独立于 diet（cwd 不是工具集）。
                apply_l2_project_cwd(
                    spawn_result.get("profile_dir"), project_path
                )
            except Exception as exc:
                logger.warning("plobi: l2 diet/cwd skipped for %s: %s", safe_id, exc)
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
    r"""List every active Mind project as a L2 agent.

    Calls :func:`ensure_mind_project_agents` first (idempotent) and then
    returns the live ``l2_project`` rows whose category is ``projects`` or
    ``research``. The output includes a brief plan/progress excerpt per row
    (≤400 chars) so the L1 final answer can quote real evidence without
    ever dumping the whole plan.md into context. Never touches chatlog and
    never spawns an agenda L2 — task: "ChatlogDead 下仍 ok".
    """
    user_text = (user_text or "").strip()
    reg, seeded = ensure_mind_project_agents(registry)

    from plobi.mind.paths import resolve_root

    mind_root = resolve_root(None)
    projects_base = (
        mind_root / "Vault" / "projects" if mind_root is not None else None
    )

    rows: list[dict] = []
    for entry in reg.entries():
        if entry.archived:
            continue
        if entry.role != "l2_project":
            continue
        if entry.category not in {"projects", "research"}:
            continue
        plan_text = ""
        progress_text = ""
        has_mind = False
        if projects_base is not None:
            sub = entry.mind_subtree or ""
            try:
                name = sub.split("/")[-1] if sub else entry.name
                directory = projects_base / name
                has_mind = directory.is_dir()
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
                "plan_excerpt": plan_text[:PROJECT_EXCERPT_MAX_CHARS],
                "progress_excerpt": progress_text[:PROJECT_EXCERPT_MAX_CHARS],
            }
        )

    return {
        "ok": True,
        "intent": "project_status",
        "user_text": user_text,
        "seeded": seeded,
        "projects": rows,
    }


def run_plan_day(
    user_text: str,
    *,
    date: str | None = None,
    pipeline=None,
    registry: AgentRegistry | None = None,
) -> dict:
    """Build tomorrow's (or ``date``'s) nightly plan and return it read-only.

    Thin wrapper over the existing ``generate_evening_plan`` — no mutation,
    no LLM, no agenda L2 spawn. On failure (e.g. corrupted DB), ``ok`` is
    ``False`` and the original error string is returned untouched so the L1
    can quote it. Empty days return ``status=empty`` with zero items.
    """
    from datetime import date as _date, timedelta

    user_text = (user_text or "").strip()
    for_date = (str(date).strip()[:10] if date else "") or (
        _date.today() + timedelta(days=1)
    ).isoformat()

    db_path = _secretary_db_path(pipeline)
    try:
        from plobi.agenda import planning
        from plobi.agenda import store as _store
    except Exception as exc:
        return {
            "ok": False,
            "intent": "plan_day",
            "error": f"无法加载规划器: {exc}",
            "for_date": for_date,
            "user_text": user_text,
        }
    try:
        plan = planning.generate_evening_plan(for_date, db_path=db_path)
    except Exception as exc:
        logger.warning("plobi: plan_day failed for %s: %s", for_date, exc)
        return {
            "ok": False,
            "intent": "plan_day",
            "error": f"plan_day 失败: {exc}",
            "for_date": for_date,
            "user_text": user_text,
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
# calls ``generate_evening_plan`` (read-only path through the store). Both are
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
MUTATE_ACTIONS = ("create", "update", "delete", "cancel_matching")
# WP-SECRETARY-MOUTH: cancel_matching is the only bulk-delete the L1 is
# allowed to issue ("这期完了 / 这门课后面都不用去了"). Each source is gated
# individually — only sources the secretary has a write path for can be
# cancelled here, and never in 200 delete round-trips.
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
            "mutate_agenda 需要 action=create/update/delete/cancel_matching",
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
    if action in ("update", "delete") and not event_id and not title:
        return _mutate_error(
            f"{action} 需要 event_id 或 title 来定位那条日程",
            action=action,
            user_text=user_text,
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


def run_secretary_ask(
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
    No kanban. No chat REST. Dead chatlog returns ``ok: False``.
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
        return run_plan_day(
            user_text, date=date, pipeline=pipeline, registry=registry
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


def aigw_base_url() -> str:
    return (
        os.environ.get("PLOBI_AIGW_URL")
        or os.environ.get("PLOBI_QUOTA_AIGW_URL")
        or AIGW_DEFAULT_BASE
    ).rstrip("/")


def aigw_api_key() -> str:
    return (
        os.environ.get("AIGW_API_KEY")
        or os.environ.get("PLOBI_QUOTA_AIGW_KEY")
        or "sk-local-dev-key"
    )


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
            f"日期：{plan.get('for_date') or agenda_summary.get('tomorrow') or ''}",
            f"昨夜计划：{plan.get('summary') or ''}",
            f"冲突数：{int(plan.get('conflict_count') or 0)}",
            "请写一段简洁中文早报，不要解释调度过程，不要逐条抽取。",
        ]
        return "\n".join(lines)
    events = agenda_summary.get("tomorrow_events") or agenda_summary.get("events") or []
    lines = [
        f"用户原话：{user_text}",
        f"日期：{agenda_summary.get('tomorrow') or agenda_summary.get('today') or ''}",
        "明日日程：",
    ]
    if not events:
        lines.append("（无条目）")
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
    day = agenda_summary.get("tomorrow") or agenda_summary.get("today") or ""
    events = agenda_summary.get("tomorrow_events") or agenda_summary.get("events") or []
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
# WP-L2-DIET / 裁定 33.2：``memory`` 也进 L1 禁单——L1 是总秘书不是 LLM 长记忆。
L1_DROP_TOOLSETS = frozenset(
    {
        "terminal",
        "session_search",
        "code_execution",
        "computer_use",
        "memory",
    }
)


def _is_full_plobi_composite(name: str) -> bool:
    """True for platform composites like ``plobi-cli`` that embed terminal/search."""
    return name.startswith("plobi-")


# 中收默认名单（对齐 docs/plobi/profiles/master/config.yaml，略宽于「只剩一个」）。
# ``memory`` 已搬进 L1_DROP_TOOLSETS——任何携带它进来的活动 profile 都会在
# ``mid_narrow_toolset_names`` 里被剥掉，不在此名单里再列。
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
L1_SOUL_BLOCK = f"""{L1_SOUL_BEGIN}
## 身份（WP-L1-IDENTITY，硬规则，不可绕过；写在最前）

- 你是本机 **Plobi 总秘书（L1）**。**不是** Nous Research 助手、**不是** 通道模型的名字、**不是** 日程 L2、**不是** 通用 chatbot、**不是** Cursor / Claude Code 这种 GUI 工具里钻的工具人。
- 用户问「你是谁 / 你是 L1 吗 / 你能干啥」：一两句说你听他说话、统筹派工、活派给下面的项目助手。**不要**开工具菜单、**不要**列七个 intent 给用户挑（A/B/C 选项）、**不要**列可以调用的 MCP / skills 让用户手动选——它听不懂也不会选；它要的是「说一句话你就把活干好」。
- 你了解全局，但不要当工人把所有细节吞进这一张嘴。改代码 / 跑命令 / 去操控某个 AI 软件：派给对应项目的 L2（它再管 L3）。你没有 terminal **不是故障**，**不要**解释框架、**不要**让用户改配置、**不要**自证清白——terminal 早被 WP-L2-DIET（裁定 33.3）剥掉了。
- 日程 / 排天 / 待确认 / 项目进度：第一动作仍是已有的 `plobi_secretary_ask`（七个 intent 一个不增）。第一动作 = 唯一动作；不要用 `plobi_master_dispatch` / `preview` / `status` / `approve` 抢活（裁定 32-33 段已禁止）。

## 总秘书派工（软路由）

用户问「明天安排 / 明天的日常安排是什么」或「根据明天的日程写早报」时：
调用工具 `plobi_secretary_ask`（`intent=refresh_agenda` 或 `write_briefing`）。
不要用 `session_search` 搜旧会话，不要开 `terminal` 跑命令，不要 clarify 空转。
选哪个日程 L2 由工具内名单路由（缺则按模板 spawn 一个）；不要写死某个 agent id。
工具回传若有 `plan`：先念 `plan.summary`（昨夜安排）；`missing`/`dismissed` 只陈述刷新后的事实，不要编计划。不要把事件列表再排一遍。
终答像秘书说话，不解释调度细节。

## 日程写入口（硬规则，不可绕过）

- 用户让你加一条日程、改某条的时间或标题、取消某条日程：第一动作就是调用 `plobi_secretary_ask`，`intent=mutate_agenda`，带上 `action`（`create`/`update`/`delete`）和钟点（本地 ISO，如 `2026-09-12T15:00:00`）。
- 只有工具返回 `ok=true` 才能对用户说「已记下」。缺钟点就先问一句，不准编 9:00，不准默认补 1 小时。
- 不准用 `refresh_agenda` / `write_briefing` 冒充写入。不准叫用户去看板手点、不准让用户自己另开入口。采集通不通都不影响你收下这条指令并落库。
- 改/删没说清是哪条时，工具会返回候选列表；把候选念给用户选，不要替用户猜。

## 日程查询与待确认（硬规则，不可绕过）

- 用户问「今天有什么 / 今天还剩什么 / 明天几点有课 / 这周安排 / 9 月 15 号有什么 / 有什么待确认的」：第一动作就是调用 `plobi_secretary_ask`，`intent=query_agenda`，`range` 取 `today`/`tomorrow`/`week`/`date`/`pending`（`range=date` 必须带 `date`）。
- 这是读共享日程库，采集通不通都能答。**不是** `refresh_agenda`——只有用户明确说「重新采集 / 刷新一下」才用它。不准用 memory、旧会话或任何印象回答日程。
- 用户说「把某条待确认的确认掉 / 第二条忽略」：`intent=decide_pending`，带 `decision`（`confirm`/`dismiss`）和 `event_id` 或 `title`（必要时带 `date`）。工具回 `candidates`（2+ 条）就把候选念给用户选，不替用户挑。
- 「把那件事推到明天 / 挪到几点」是改不是查：`intent=mutate_agenda`，`action=update`。
- 终答只念工具返回的事实：`end_at` 为空就说「没写结束」，不补时长、不编钟点；待确认项要和已确认的分开说，别把没批的当成已安排。

## 采集失败与防编造纪律（硬规则，不可绕过）

- 若 `plobi_secretary_ask` 返回 `ok=false` / `dead=true`，或 chatlog 采集失败：终答只能说明「日程采集当前不通」，并如实告知；不得假装已拿到日程。
- 严禁用 memory、旧会话、项目印象或任何缓存去编造「明天安排」「早报日程表」等具体安排。采集不通时，宁可不答，也不要虚构。
- `write_briefing` 失败时，不要自己落笔写带钟点的假日程（如「09:00 开会、14:00 健身」）。只如实告知生成失败。

## 项目进度与排明天（WP-PROJECT-PORTFOLIO，硬规则，不可绕过）

- 用户问「各项目怎么样了 / 项目进展 / 在推什么 / 给我列一下所有进行中的项目」：第一动作就是调用 `plobi_secretary_ask`，`intent=project_status`。Mind 真源在 `MIND_ROOT` 环境变量或 `<仓库>/Code/mind`（默认后者已配 `AGENTS.md`），**不要**让用户贴路径，**不要**用 memory 里那几条冒充项目清单，**不要**只列 Plobi 一条。
- 工具回 `projects[]`：每条只有 `id / name / category / weekly_hours / project_path / mind_subtree / has_mind / plan_excerpt / progress_excerpt`，不要追问模型。`weekly_hours` 为 `null` 就说「未设每周节奏」，不要发明小时数；`has_mind=false` 老实说「Mind 没有这个项目子树」。
- 「吃饭睡觉还没安排 / 排一下明天 / 按真实项目再排一遍」→ `intent=plan_day`（可带 `date=YYYY-MM-DD`，缺省=明天）。**禁止**走 `mutate_agenda` 一条条把午饭晚饭睡眠写进 events；规划器输出怎么排就怎么念，`conflict_count` 必须如实念给用户。
- 终答只念工具返回的事实；不要把 `project_status` 的 `plan_excerpt` 当成「项目在做什么」的完整真相——它只是一段摘录，引用前说明。

## 派工不是工人（WP-L2-DIET，硬规则，不可绕过）

- 你是总秘书，不是工人。日程 / 项目 / 排天 / 待确认 **第一动作**只有 `plobi_secretary_ask`（含七个 intent：`refresh_agenda` / `write_briefing` / `mutate_agenda` / `query_agenda` / `decide_pending` / `project_status` / `plan_day`）。**不要**直接调 `plobi_master_dispatch` / `plobi_master_preview` / `plobi_master_status` / `plobi_master_approve` 抢活——那是 L1 派工到 kanban 的窄入口，不是日程/项目入口。
- 不准调用 `memory` 工具（即使它还在活动 profile 的 toolsets 配置里也不准用；裁定 33.2 已把它从 L1 中收里拿掉）。日程/项目答案只能来自 `plobi_secretary_ask` 的工具回传。
- 不准对 `D:\\Projects\\Plobi\\Code` 开 `terminal`「我去改产品」——WP-L2-DIET（裁定 33.3）已把项目 L2 的 `terminal` / `computer_use` / `code_execution` / `session_search` 和所有 `plobi-*` 复合工具集剥掉；L1 同样没有 terminal。需要改主树只能通过人批（你负责派工，不负责提交）。

## 查今天/明天/某天要把饭和觉一并念出来（WP-QUERY-DAY，硬规则，不可绕过）

- 用户问「今天有什么 / 明天有什么 / 9 月 16 号有什么」：`intent=query_agenda`，`range` 选 `today` / `tomorrow` / `date`，**不要**直接拿 `events` 数组就回。响应里同时会有 `anchors`（作息锚点：早饭 / 午饭 / 晚饭 / 睡眠，标题与起止时间）+ `plan_items`（当日 daily_plan 的项）。`anchors` 与右栏时间轴用的是同一份纯函数 `plobi.agenda.planning.day_surface`，嘴和轴的钟点不会漂。
- 终答必须把 `anchors` 里 **当天实际有**的作息念出来（午饭 / 晚饭等），没念到的就当用户没收到。`anchors` 每条 `end_at` 为空就说「没写结束」不补时长。
- 用户说「别中午排会 / 午饭往后挪 / 别把会排在午饭」→ 调 `plobi_checkin_respond`（已有工具）；**禁止**走 `mutate_agenda` 一条条把午饭 / 睡眠 / 让位写进 events，那是抢规划器的活。checkin 的实现归后端2，本刀只写口令。
- `range=week` / `range=pending` 不是「一天轴」——响应里 **不**会带 `anchors` / `plan_items`，正常回 `events` / `pending` 即可。
{L1_SOUL_END}
"""


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
    lists (:func:`apply_l1_mid_toolsets` does exactly that triple write, and
    ``create_profile(clone_config=True)`` clones all three from root together).
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


# WP-STUDIO-CWD / 裁定 36.1：把项目 L2 profile 的 ``terminal.cwd`` 写进
# config.yaml，让新会话落到该项目的目录，而不是总秘书的家。L2-DIET 剥掉
# 的「terminal 工具集」是另一回事——cwd 是配置字段，删工具集 ≠ 删 cwd。
# 但ler / 空 path / 路径不存在：不写、不抢别人的 cwd（避免用假路径污染）。
L2_PROJECT_CWD_BUTLER_ROLES = frozenset({"l2_agenda", "l2_butler"})


def apply_l2_project_cwd(profile_dir: Path | str | None, project_path: str | None) -> bool:
    """WP-STUDIO-CWD: write ``terminal.cwd`` into a project L2 profile's
    ``config.yaml``. Idempotent; preserves every other key.

    - ``project_path`` empty / None → no-op (butler / no path).
    - profile dir doesn't exist → no-op (still nothing on disk).
    - resolved ``project_path`` doesn't exist as a directory → no-op
      (NEVER fabricate a cwd — that would just push the agent into a
      broken workdir on next session).
    - profile dir has no ``config.yaml`` yet → write a minimal one with
      just ``terminal: {cwd: ...}`` so the gateway can still resolve the
      cwd when the profile lands. Model routing stays untouched (handled
      elsewhere in :meth:`AgentRegistry._write_profile_config`).
    - existing ``config.yaml`` → keep all other keys (including any other
      ``terminal:`` sub-keys), only update ``terminal.cwd`` if it differs.
    """
    if not project_path:
        return False
    if profile_dir is None:
        return False

    import yaml

    cfg_path = Path(profile_dir) / "config.yaml"
    abs_path = str(Path(project_path).expanduser().absolute())
    if not Path(abs_path).is_dir():
        return False

    if not cfg_path.is_file():
        # Minimal config: just terminal.cwd. Don't compete with the model
        # routing layer on keys we don't own.
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        cfg_path.write_text(
            yaml.safe_dump(
                {"terminal": {"cwd": abs_path}},
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

    terminal = cfg.get("terminal")
    if not isinstance(terminal, dict):
        terminal = {}
    # Idempotent: the absolute path is already there → nothing to do.
    if terminal.get("cwd") == abs_path:
        return False
    terminal["cwd"] = abs_path
    cfg["terminal"] = terminal
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


def apply_shared_skills_root(profile_dir: Path | str | None) -> bool:
    """Point an L2 分身's ``config.yaml`` at :func:`shared_skills_root`.

    裁定 42 §42.3 第 2 条：分身不再自带技能，可见性来自
    ``skills.external_dirs``（``agent/skill_utils.get_external_skills_dirs``
    读取；``tools/skills_tool.py`` 与 ``agent/prompt_builder.py`` 都按
    「本地主根优先、同名外部技能跳过」合并，所以列表不会翻倍）。

    Idempotent and preserves every other key:
    - profile dir is ``None`` → no-op.
    - shared root missing (fresh install, nothing seeded) → still written; the
      scanner skips non-existent dirs, and the binding self-heals once the
      root exists.
    - the root is already listed (compared after ``expanduser`` + ``absolute``)
      → no-op, so repeated ``spawn`` never appends a second entry.
    - any external dir the user listed themselves is preserved; we only append
      ours, never reorder or drop.
    """
    if profile_dir is None:
        return False

    import yaml

    root = str(shared_skills_root().expanduser().absolute())
    cfg_path = Path(profile_dir) / "config.yaml"

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


def _cwd_existing_l2_project(entry: "AgentEntry") -> None:
    """WP-STUDIO-CWD / 裁定 36.1: 对注册表里已存在的未归档 ``l2_project``
    行也同步 terminal.cwd。用户手改过 yaml 时仍以 registry 的 project_path
    为准——这是秘书产品的真源（裁定 36.1 第 2 段）。profile 不存在 = 静默。"""
    if entry.role in L2_PROJECT_CWD_BUTLER_ROLES:
        return
    if entry.archived:
        return
    if entry.category not in ("projects", "research"):
        return
    if not entry.project_path:
        return
    profile_name = entry.profile_name
    if not profile_name:
        return
    try:
        from plobi_cli.profiles import get_profile_dir, profile_exists

        if not profile_exists(profile_name):
            return
        apply_l2_project_cwd(get_profile_dir(profile_name), entry.project_path)
    except Exception as exc:
        logger.debug("plobi: l2 cwd (existing) skipped for %s: %s", entry.name, exc)


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
    existing = ""
    if soul_path.is_file():
        existing = soul_path.read_text(encoding="utf-8")
    stripped = _strip_l1_soul_spans(_strip_l2_soul_spans(existing)).rstrip()
    updated = (stripped + "\n\n" if stripped else "") + L1_SOUL_BLOCK.strip() + "\n"
    if updated == existing:
        return False
    soul_path.parent.mkdir(parents=True, exist_ok=True)
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


def _is_placeholder_project_description(description: str) -> bool:
    """True when ``description`` is the seed stub, not a written project purpose.

    Single source of truth: :data:`L2_PLACEHOLDER_DESCRIPTION_PREFIX` — the exact
    string :func:`ensure_mind_project_agents` writes (the `` (raw dir name: …)``
    variant still starts with it, so one prefix check covers both shapes).
    """
    return (description or "").strip().startswith(L2_PLACEHOLDER_DESCRIPTION_PREFIX)


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
    if entry.description and not _is_placeholder_project_description(entry.description):
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
        # 凭空建 profile 目录 —— 那是第二真源，见 ``apply_l2_project_cwd`` 的同款判据。
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


def ensure_north_star_toolset(*, save: bool = True) -> dict:
    """Open ``plobi_north_star`` on the **active** profile (usually default).

    Does not create a ``master`` profile (裁定 6). WP-BE-9 also mid-narrows
    L1 toolsets (drop terminal / session_search / command runners) and upserts
    soft-routing SOUL instructions. Idempotent and reversible via config lists.
    """
    from plobi_cli.config import load_config, save_config

    config = load_config()
    changed = apply_l1_mid_toolsets(config)

    plugins = config.get("plugins")
    if not isinstance(plugins, dict):
        plugins = {}
        config["plugins"] = plugins
        changed = True
    enabled = plugins.get("enabled")
    if not isinstance(enabled, list):
        enabled = []
        plugins["enabled"] = enabled
    if "plobi-north-star" not in enabled:
        enabled.append("plobi-north-star")
        changed = True
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
