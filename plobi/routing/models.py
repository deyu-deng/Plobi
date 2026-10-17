"""Role → model routing.

Turns the cost discipline in docs/adr/0011-three-tier-agents-and-model-routing.md
into something the code can enforce:

- **L1** runs the best available model (currently Kimi K3) and only ever
  statuses, decides, delegates. It must never be pointed at a free-tier GUI
  surface, and never at the same model as L2 (that would mean the expensive
  model is doing clerical work).
- **L2** runs cheap models or aggregated quota and does the per-message
  classification, extraction and tool loops.
- **L3** spends no tokens at all — it drives GUIs.

Config: ``$PLOBI_HOME/plobi/models.json`` (ids only; API keys stay in env,
per AGENTS.md).
"""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

L1_SECRETARY = "l1_secretary"
L2_AGENDA = "l2_agenda"
L2_PLANNER = "l2_planner"
L2_PROJECT = "l2_project"
# ``l2_course``（用户 10-11 拍：五门课要进名册，并且"再加一个角色"）。课业条目是**数据**，
# 不是分身：它故意**不进 ``DEFAULT_ROUTES``**（没有模型路由 ⇒ ``resolve()`` 会抛 RoutingError）。
# 三个 ``resolve(entry.role)`` 调用点都验过：``plobi/agents/cli.py:85`` 与
# ``plobi/agents/registry.py:889`` 各自有 try/except 兜住，``registry.py:732`` 只在条目带
# model override 时才走到（课程条目不带）。它也不是 ``ASKABLE_ROLE`` ⇒ L1 不会去"问一门课干活"，
# 作业归属靠的是条目名（``planning.py::_matches_project`` 读 ``evidence.course``），不靠分身。
L2_COURSE = "l2_course"

KNOWN_ROLES = (L1_SECRETARY, L2_AGENDA, L2_PLANNER, L2_PROJECT, L2_COURSE)

# Surfaces whose quota cannot be converted into an API key. They belong to L3
# workers; wiring one to L1 would make the secretary depend on GUI automation.
GUI_ONLY_SURFACES = ("marvis", "cursor", "antigravity-gui", "workbuddy", "gui")

# ``aigw`` is the local OpenAI-compatible gateway (WorkBuddy quota etc.).
# L2 generation goes through aigw model ids like ``workbuddy/deepseek-chat``
# (MVP §8.2 L3a). Direct ``deepseek`` API routes are retired (balance exhausted).
DEFAULT_ROUTES: dict[str, dict[str, str]] = {
    L1_SECRETARY: {"provider": "moonshot", "model": "kimi-k3"},
    L2_AGENDA: {"provider": "aigw", "model": "workbuddy/deepseek-chat"},
    L2_PLANNER: {"provider": "aigw", "model": "workbuddy/deepseek-chat"},
    L2_PROJECT: {"provider": "aigw", "model": "workbuddy/deepseek-chat"},
}


class RoutingError(ValueError):
    pass


def shared_config_path() -> Path:
    """路由表的**唯一写入落点**：共享层那一份，不管当前活跃家是谁。

    裁定 42 把分身降成「名册一行 + 最多三个覆盖」，裁定 166 把这句变成可验收工作：
    角色→模型那张表本来就该只有一份，而现核它被复印了 **23 份、去重后内容只有 1 份**
    （顶层那一份反倒不存在）。写侧一律指到这里，复印件就不再新增。
    """
    try:
        from plobi_constants import get_shared_plobi_root

        return get_shared_plobi_root() / "plobi" / "models.json"
    except Exception:
        return Path.home() / ".plobi" / "plobi" / "models.json"


def config_path() -> Path:
    """读侧落点：**共享那一份优先**，没有才回落到自己家（兼容还没搬走的复印件）。

    顺序就是裁 166 决定一的形状——名册行上的覆盖由
    :meth:`plobi.agents.registry.AgentRegistry.router` 叠在这张表之上，所以生效顺序是
    「名册行覆盖 → 共享层那一份 → 自家复印件（旧数据兼容）→ :data:`DEFAULT_ROUTES`」。
    共享那份存在时**自家那份不再赢**，这一条就是「配一次模型大家都能用」的机制，
    不是给分身加继承——裁 42 的本意正是这个，``Code/AGENTS.md`` 那句
    「profiles are independent islands」已被推翻（裁 166 明写：以它为由回退即抗命）。
    """
    override = os.environ.get("PLOBI_MODELS_CONFIG", "").strip()
    if override:
        return Path(override)
    shared = shared_config_path()
    if shared.exists():
        return shared
    try:
        from plobi_constants import get_plobi_home

        root = get_plobi_home() / "plobi"
    except Exception:
        root = Path.home() / ".plobi" / "plobi"
    return root / "models.json"


@dataclass(frozen=True)
class ModelRoute:
    role: str
    provider: str = ""
    model: str = ""

    @property
    def configured(self) -> bool:
        return bool(self.model)

    @property
    def qualified(self) -> str:
        if self.provider and self.model:
            return f"{self.provider}/{self.model}"
        return self.model or self.provider

    @property
    def is_gui_surface(self) -> bool:
        provider = (self.provider or "").lower().strip()
        model = (self.model or "").lower().strip()
        # aigw exposes desktop-quota model ids (e.g. workbuddy/*) as an OpenAI-
        # compatible API. That is L3a for generation — not a HID/GUI surface.
        if provider == "aigw":
            return False
        blob = f"{provider} {model}"
        return any(surface in blob for surface in GUI_ONLY_SURFACES)


@dataclass
class ModelRouter:
    routes: dict[str, ModelRoute] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path | str | None = None) -> "ModelRouter":
        target = Path(path) if path else config_path()
        raw: dict = {}
        if target.exists():
            try:
                loaded = json.loads(target.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    raw = loaded.get("roles") if isinstance(loaded.get("roles"), dict) else loaded
            except (OSError, json.JSONDecodeError):
                logger.warning("plobi: could not read %s; using defaults", target)

        routes: dict[str, ModelRoute] = {}
        for role, defaults in DEFAULT_ROUTES.items():
            entry = raw.get(role) if isinstance(raw.get(role), dict) else {}
            routes[role] = ModelRoute(
                role=role,
                provider=str(entry.get("provider") or defaults["provider"]),
                model=str(entry.get("model") or defaults["model"]),
            )

        # Roles the operator invented (extra project agents) are kept as-is.
        for role, entry in raw.items():
            if role in routes or not isinstance(entry, dict):
                continue
            routes[role] = ModelRoute(
                role=role,
                provider=str(entry.get("provider") or ""),
                model=str(entry.get("model") or ""),
            )

        return cls(routes=routes)

    def save(self, path: Path | str | None = None) -> Path:
        # 写侧不跟随读侧的回落：默认只写共享层那一份，否则「存一次」会变成「复印一次」。
        target = Path(path) if path else shared_config_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        data = json.dumps(
            {
                "roles": {
                    role: {"provider": route.provider, "model": route.model}
                    for role, route in sorted(self.routes.items())
                }
            },
            ensure_ascii=False,
            indent=2,
        )
        # Atomic write: write to temp file then rename.
        tmp = target.with_suffix(".tmp")
        try:
            tmp.write_text(data, encoding="utf-8")
            tmp.replace(target)
        except BaseException:
            try:
                tmp.unlink()
            except OSError:
                pass
            raise
        return target

    def resolve(self, role: str) -> ModelRoute:
        route = self.routes.get(role)
        if route is None:
            raise RoutingError(f"no model route configured for role {role!r}")
        return route

    def is_l1(self, role: str) -> bool:
        return role == L1_SECRETARY

    def violations(self) -> list[str]:
        """Contract checks. Empty list means the routing obeys ADR-0011."""
        problems: list[str] = []

        l1 = self.routes.get(L1_SECRETARY)
        if l1 is None or not l1.configured:
            problems.append("l1_secretary has no model configured")
            return problems

        if l1.is_gui_surface:
            problems.append(
                f"l1_secretary points at a GUI-only surface ({l1.qualified}); "
                "those belong to L3 workers"
            )

        for role, route in self.routes.items():
            if role == L1_SECRETARY:
                continue
            if not route.configured:
                problems.append(f"{role} has no model configured")
                continue
            if route.qualified == l1.qualified:
                problems.append(
                    f"{role} shares L1's model ({route.qualified}); the expensive "
                    "model would end up doing per-message work"
                )

        return problems

    def assert_valid(self) -> None:
        problems = self.violations()
        if problems:
            raise RoutingError("; ".join(problems))


_DEFAULT: Optional[ModelRouter] = None
_DEFAULT_LOCK = threading.Lock()


def get_router() -> ModelRouter:
    global _DEFAULT
    if _DEFAULT is None:
        with _DEFAULT_LOCK:
            if _DEFAULT is None:
                _DEFAULT = ModelRouter.load()
                for problem in _DEFAULT.violations():
                    logger.warning("plobi model routing: %s", problem)
                # Warn if any default-route provider is not discoverable.
                _warn_missing_providers(_DEFAULT)
    return _DEFAULT


def _warn_missing_providers(router: ModelRouter) -> None:
    """Log a warning if a default-route provider has no plugin registered."""
    try:
        from agent.auxiliary_client import _PROVIDER_ALIASES  # type: ignore[attr-defined]

        known_providers = set(_PROVIDER_ALIASES.values())
    except Exception:
        known_providers = set()

    # Best-effort: also check provider plugin discovery.
    try:
        from plugins.model_providers import providers  # type: ignore[import-untyped]

        for profile in providers._PROVIDER_PROFILES.values():  # type: ignore[attr-defined]
            known_providers.add(profile.name)
    except Exception:
        pass

    if not known_providers:
        return

    for role, route in router.routes.items():
        if route.configured and route.provider not in known_providers:
            # Check aliases too (e.g. "moonshot" → "kimi-coding").
            try:
                from agent.auxiliary_client import _PROVIDER_ALIASES  # type: ignore[attr-defined]

                if route.provider in _PROVIDER_ALIASES:
                    continue
            except Exception:
                pass
            logger.warning(
                "plobi model routing: %s provider %r is not registered as a "
                "built-in alias or plugin; route may fail at runtime. "
                "Install the provider plugin or change the route in models.json.",
                role,
                route.provider,
            )


def set_router(router: Optional[ModelRouter]) -> None:
    global _DEFAULT
    _DEFAULT = router
