"""Role → model routing.

Turns the cost discipline in docs/adr/0011-three-tier-agents-and-model-routing.md
into something the code can enforce:

- **L1** runs the best available model (currently Kimi K3) and only ever
  statuses, decides, delegates. It must never be pointed at a free-tier GUI
  surface, and never at the same model as L2 (that would mean the expensive
  model is doing clerical work).
- **L2** runs cheap models or aggregated quota and does the per-message
  classification, extraction and tool loops.
- **L3** is *not* a role in this table. Temporary experts and offloaded
  external AI software are full agents with their own models, tool loops and
  the ability to spawn ordinary sub-agents (11-04 ruling; see
  ``plobi/delegation/expert_roles.py`` and ``agent/external_offload.py``).
  Their models are chosen per expert role / per external software, never here.
  Pico HID / ``computer_use`` are input channels, not a definition of L3.

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
from typing import Iterable, Optional

logger = logging.getLogger(__name__)

# 常量读的是**身份档位**（glossary.md 四词），值就是名字本身、只有两个。
#
# 2026-11-15（用户裁定「role 只有两个值，第三值删掉」）：
#   两个旧字面量跟着改名：``l1_secretary`` → ``conductor``；``l2_project`` → ``principal``。
#   第三值 ``l2_course`` 物理删除。
#
# 原来值照旧的理由是「名册真数据 + 251 处判据存的都是旧字面量，改值等于数据迁移」。
# 现在改，是因为旧值自己在撒谎：``l2_project`` 只是五种 category 之一，却被当成
# 整个首席档的名字；而 ``l2_course`` 描述的那件事（这一行**还没有**身份）根本不是
# 一个角色，它是 :func:`plobi.agents.registry.derive_runtime_slug` 返回空串这一个
# 事实的第二张皮——同一个语义存两处，两处就会各走一边（10-20 那次「课程是数据、
# 不可点」的误判就是这么来的）。
#
# 从 此「有没有身份」只有一个判据：名册名派生出的运行时 slug 是不是空串。
# 课程条目没有身份时自然派生不出 slug；有了 ``category: courses`` ＋ 显式 ASCII
# slug 就和项目分身同列同点（裁定 143② / 145①(b) / 146②(b)）。
CONDUCTOR = "conductor"   # 指挥
PRINCIPAL = "principal"   # 首席（五类 category 一律挂这一档）

#: 名册里认得出的角色只有这两个。名手的四类（``plobi/delegation/expert_roles.py``
#: 的 ``TRADES``）与客席（``agent/external_offload.py``）都不是本表的值。
KNOWN_ROLES = (CONDUCTOR, PRINCIPAL)

# ``bezel`` is the local OpenAI-compatible gateway (WorkBuddy / OpenCode quota
# and friends behind one loopback endpoint). L2 generation goes through bezel
# model ids like ``workbuddy/deepseek-v4.1-flash``; the first path segment names
# the software that actually serves the leg — that is the picker's provider row,
# and :attr:`ModelRoute.display_model` is the model row.
#
# 2026-11-14 用户裁定：**不存在 L2 三角色**。名册里的五类 L2（projects /
# butler / events / research / courses）是平行的，role 一律是 ``PRINCIPAL``，
# 各自的模型覆盖写在名册行上（裁 42），所以这里只有一格 L2 默认。
# 2026-11-15 收口：``l2_agenda`` / ``l2_planner`` 一并退场。日程不是特权角色，
# 它是名册上 category=butler 的普通一行；「三档 L2」更是不存在的结构。
# 从此这张表只有两格：一个指挥、一个首席。
#
# The gateway is a model/provider route for L2 generation — it is not an L3
# agent (ruling 205). Direct ``deepseek`` API routes are retired.
DEFAULT_ROUTES: dict[str, dict[str, str]] = {
    CONDUCTOR: {"provider": "moonshot", "model": "kimi-k3"},
    PRINCIPAL: {"provider": "workbuddy", "model": "workbuddy/deepseek-v4.1-flash"},
}

# 2026-11-15：读侧迁移代码全部退场。上一刀为网关旧机器标识 → ``bezel``、假模型 id
# ``workbuddy/deepseek-chat``、「三档 L2」的 ``l2_planner`` 各留了一张兼容表；用户裁定
# 「内测版没有用户，不用管老配置文件，不接受为兼容付代价」——所以三张表连同
# :func:`_migrated_provider` 一起删掉。写侧本来就只出新值，读侧不再替旧值兜底。
# 2026-11-19 再收一刀：最老那两代机器标识连同读侧兼容壳一并剔除（不留任何识别路径）。
# 真机核查（11-15）：本机没有生产 ``models.json``，只有 pytest 临时目录里的夹具残渣。


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
    def upstream(self) -> str:
        """The software that actually serves this leg (``workbuddy/x`` → workbuddy).

        The gateway's model ids are ``<upstream>/<model>``. That first segment is
        what the picker shows as the *provider* row; the gateway slug itself is
        plumbing and never reaches a list row (README 裁定 45.4).
        """
        head, sep, _rest = (self.model or "").partition("/")
        return head.strip().lower() if sep else ""

    @property
    def display_model(self) -> str:
        """The model row text: the real model name, without the upstream prefix.

        2026-11-14 用户拍：模型名上屏不要带 ``workbuddy``。带上之后那一行读起来
        像第二个提供商名（当时框里是 ``workbuddy/hy4free``），而甲/乙两案里他
        选了乙 —— 只留真实模型名。请求层不受影响：发出去的仍是完整复合 id
        （:attr:`model`），剥前缀只发生在渲染层。
        """
        head, sep, rest = (self.model or "").partition("/")
        if sep and rest:
            return rest
        return self.model or ""


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
            provider = str(entry.get("provider") or defaults["provider"])
            model = str(entry.get("model") or defaults["model"])
            routes[role] = ModelRoute(role=role, provider=provider, model=model)

        # 名册里认得出的角色只有两个（:data:`KNOWN_ROLES`），盘上多出来的键**一律丢**。
        # 2026-11-19 用户原话「怎么还有 role=-projects 的东西呀。不是说了 projects 只是
        # principal 里面的一个分类吗？不要做旧数据兼容层了」：上一版把不认识的键当
        # 「operator invented roles」原样收下，于是四代退休档位（``l1_secretary``／
        # ``l2_agenda``／``l2_planner``／``l2_project``）在设置页列着、被 ``save()`` 一遍遍
        # 写回去——而 ``l2_project`` 本来只是 ``principal`` 的一个 category，不是角色。
        # 这里不做迁移、不做别名：内测版没有用户，旧配置文件不值得留一条读侧分支。
        dropped = sorted(k for k in raw if k not in routes and isinstance(raw.get(k), dict))
        if dropped:
            logger.info("plobi: dropping %d retired model-route key(s): %s",
                        len(dropped), ", ".join(dropped))

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
                    # 只写得出这两个角色（:data:`KNOWN_ROLES`）。读侧已经丢掉退休键，
                    # 写侧再挡一道：谁把脏键塞进 ``routes`` 都不会被持久化——
                    # 「配一次大家都能用」不能变成「复印一次退档一次」。
                    if role in KNOWN_ROLES
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
        return role == CONDUCTOR

    def violations(self, known_models: Optional[Iterable[str]] = None) -> list[str]:
        """Contract checks. Empty list means the routing obeys ADR-0011.

        ``known_models`` is the upstream catalogue when the caller already has
        one in hand (the settings page on the path where it just fetched the
        catalogue). Passing it turns "the id looks plausible" into "the id was
        actually listed", which is the check that was missing when
        ``workbuddy/deepseek-chat`` sat in three routes for two months: nothing
        here ever asked upstream whether the model existed.
        Left as ``None`` the check is skipped — pure routing code must not go
        near the network just to answer a validity question.
        """
        problems: list[str] = []

        l1 = self.routes.get(CONDUCTOR)
        if l1 is None or not l1.configured:
            problems.append("conductor has no model configured")
            return problems

        catalogue: Optional[set[str]] = None
        if known_models is not None:
            catalogue = {m.strip() for m in known_models if m and m.strip()}

        for role, route in self.routes.items():
            if role == CONDUCTOR:
                continue
            if not route.configured:
                problems.append(f"{role} has no model configured")
                continue
            if catalogue is not None and route.model not in catalogue:
                problems.append(f"{role} points at {route.model!r}, which upstream does not list")
                continue
            if route.qualified == l1.qualified:
                problems.append(
                    f"{role} shares L1's model ({route.qualified}); the expensive "
                    "model would end up doing per-message work"
                )

        return problems

    def assert_valid(self, known_models: Optional[Iterable[str]] = None) -> None:
        problems = self.violations(known_models)
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
