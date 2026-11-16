"""``plobi plobi agents list/spawn`` CLI 处理逻辑。

薄壳 ``cmd_plobi``（在 plobi_cli/main.py）把 argparse 结果转发到这里。
表格/文案保持简洁，不发明样式。
"""

from __future__ import annotations

import sys
from typing import Any, Optional

from .registry import (
    AGENT_CATEGORIES,
    AgentEntry,
    AgentRegistry,
    DEFAULT_CATEGORY,
    RegistryError,
    default_path,
    agent_id_for,
    explain_agent_id,
    load_registry,
)
from ..routing.models import KNOWN_ROLES, PRINCIPAL


def _entry_from_args(name: str, args: Any) -> AgentEntry:
    # ``agents register`` 这一步**就是**登记（R-072）：名册成员资格在这儿盖成
    # ``True``，和「Mind 投影不是登记」的 ``registered=False`` 是同一枚章的两面。
    #
    # 这里原来还传一格 ``profile=``：那是「一个分身一个家」时代的门牌号，概念已取消
    # （裁 178 乙案／ADR-0013），而 ``args`` 上从来没有这枚旗（``--profile`` 不在
    # register 的 parser 里）——传下去只是把退役名字再写一遍。11-05 名册把这一格改成
    # 由 ``name`` 派生的运行时 slug 之后，这一行会变成 ``TypeError`` 直接崩掉登记。
    # ``--skills`` 这一格**退役**（`WP-AGENT-CAPABILITIES`，11-15）：它原是正面清单
    # 「这个 Agent 会哪些技能」，但名册现行口径是**减法**（只记关掉了哪些）⇒ 正面那格
    # 从来不生效。旗标留着（老脚本还在传它），但**不再写进名册**，并当场说清。
    #
    # 2026-11-15（用户裁定）：**建造入口必须说清类别、名册名必须派生得出 slug**。
    # 理由：``principal`` 底下五类 category 是平行的，不给类别就只能默认落进
    # ``butler``（``DEFAULT_CATEGORY``）——那是一次静默的错分类；而派生不出 slug
    # 的名字建出来的是一行点了必然失败的空壳（裁 146②(b)「不许列在那儿点不动」）。
    # 两条都在**写名册之前**拒绝，并指明改哪一格。
    declared = tuple(getattr(args, "skills", None) or ())
    if declared:
        print(
            "注意：`--skills` 这一格退役了——名册只记「这个 Agent 关掉了哪些」，"
            "正面那格从来不生效 ⇒ 这次登记没有写它。"
            "要限制这个 Agent 的技能，在设置里逐个关（`plobi skills`）。"
        )

    role_raw = str(getattr(args, "role", None) or "").strip().lower() or PRINCIPAL
    if role_raw not in KNOWN_ROLES:
        print(
            f"Error: --role 只能是 {' / '.join(KNOWN_ROLES)}，收到 {role_raw!r}。"
            "（名手/客席不是这里的值：名手见 delegation/expert_roles.py，客席见 external_offload.py）",
            file=sys.stderr,
        )
        sys.exit(2)

    category_raw = str(getattr(args, "category", None) or "").strip().lower()
    if not category_raw:
        if role_raw == PRINCIPAL:
            print(
                "Error: 登记 Principal 必须给 --category "
                f"（{' / '.join(AGENT_CATEGORIES)}）——五类是平行的，不给就只能瞎猜一个。",
                file=sys.stderr,
            )
            sys.exit(2)
        # conductor（L1）不挂分类：五类只属于 Principal 那一层，给 L1 要类别是无意义
        # 的要求（名册上它照旧写默认那一格，左栏不按 category 收它）。
        category_raw = DEFAULT_CATEGORY
    if category_raw not in AGENT_CATEGORIES:
        print(
            f"Error: --category 只能是 {' / '.join(AGENT_CATEGORIES)}，收到 {category_raw!r}。",
            file=sys.stderr,
        )
        sys.exit(2)

    # 2026-11-16（i18n 定名刀）：name 自己就是门牌的一部分，不再有「派生 / 登记
    # 一张表」这条路。建不出来只有一个原因：name 不是 ASCII。
    agent_id = agent_id_for(name, role_raw)
    if not agent_id:
        print(
            f"Error: 名册名 {name!r} 建不出可路由身份——{explain_agent_id(name, role_raw)}。",
            file=sys.stderr,
        )
        sys.exit(2)

    return AgentEntry(
        # 门牌落 name、用户看到的名字落 display_name —— 两层各归其位。用户敲中文名
        # 时 agent_id_for 会直接拒（下面那条 if），所以走到这里的 name 一定能当门牌。
        name=agent_id,
        role=role_raw,
        display_name=name if name != agent_id else "",
        category=category_raw,
        provider=getattr(args, "provider", None) or "",
        model=getattr(args, "model", None) or "",
        mind_subtree=getattr(args, "mind_subtree", None) or "",
        description=getattr(args, "description", None) or "",
        registered=True,
    )


def run_plobi(args: Any) -> None:
    action = getattr(args, "plobi_action", None)
    if action is None:
        print("usage: plobi plobi <agents> ...")
        print("  agents list              list resident Principal agents")
        print("  agents register NAME     add/update a registry entry")
        print("  agents spawn NAME        materialize the shared-layer files + model routing")
        return

    if action == "agents":
        _run_agents(args)
    else:
        print(f"Unknown plobi action: {action}", file=sys.stderr)
        sys.exit(1)


def _run_agents(args: Any) -> None:
    sub = getattr(args, "agents_action", None)
    registry = load_registry(getattr(args, "registry", None))

    if sub in (None, ""):
        print("usage: plobi plobi agents <list|register|spawn>")
        return

    if sub == "list":
        _list(registry)
    elif sub == "register":
        _register(registry, args)
    elif sub == "spawn":
        _spawn(registry, args)
    else:
        print(f"Unknown agents action: {sub}", file=sys.stderr)
        sys.exit(1)


def _list(registry: AgentRegistry) -> None:
    if not registry.agents:
        print("No resident agents registered.")
        print(f"Registry: {registry.path}")
        print("Add one:  plobi plobi agents register <name> --role principal --category butler")
        return

    problems = registry.routing_problems()
    print(f"\nRegistry: {registry.path}")
    # 那一列原来印 `Profile`（一个分身一个门牌号）——概念已取消，剩下的那格是
    # 由 name 派生的内部 slug，属机器词，不上屏（屏上文案纪律）。
    print(f"{'Agent':<16} {'Role':<14} {'Model':<24} Routing")
    print(f"{'─'*16} {'─'*14} {'─'*24} {'─'*9}")

    for entry in registry.entries():
        try:
            route = registry.route_for(entry)  # 裁 172：这一列问的是这一行，不是这一类
        except Exception:
            route = None
        model = route.qualified if route else "—"
        marker = "✅" if problems == [] else "⚠️"
        print(
            f"{entry.name:<16} {entry.role:<14} "
            f"{model:<24} {marker}"
        )

    if problems:
        print("\nADR-0011 routing violations:")
        for p in problems:
            print(f"  - {p}")
    else:
        print("\nADR-0011 routing: ✅ green")
    print()


def _register(registry: AgentRegistry, args: Any) -> None:
    name = getattr(args, "name", None)
    if not name:
        print("agents register requires a NAME", file=sys.stderr)
        sys.exit(1)
    entry = _entry_from_args(name, args)
    registry.upsert(entry)
    registry.save()
    print(f"Registered {name} (role={entry.role})")
    print(f"Registry: {registry.path}")
    print(f"Materialize: plobi plobi agents spawn {name}")


def _spawn(registry: AgentRegistry, args: Any) -> None:
    name = getattr(args, "name", None)
    if not name:
        print("agents spawn requires a NAME", file=sys.stderr)
        sys.exit(1)

    # 允许 spawn 时用 --role 等直接注册（不必先 register）。
    # `profile` 从这串里去掉：它不再是一格可写的东西（11-05 起是 name 派生的运行时
    # slug），留着等于给用户一个"写了也不生效"的假入口。
    if registry.get(name) is None and any(
        getattr(args, f, None) for f in ("role", "category", "provider", "model")
    ):
        registry.upsert(_entry_from_args(name, args))
        registry.save()

    try:
        result = registry.spawn(name)
    except RegistryError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"\nSpawned {result['name']} ({result['role']})")
    # WP-REGISTRY-NO-HOME（B 刀）：不再落一个家，所以这一行从
    # 「Profile: x → <那个家的路径>」改成「身份在名册这一行上」。
    # 11-05 再改一刀：原来读 ``result['profile']``——那是「一个分身一个门牌」的键，
    # 概念取消后那一格不再是数据而是派生物，且随时可能从返回值里消失（读了就是
    # KeyError）。人只需要知道身份在哪一行。
    print(f"Agent:    {result['name']}（身份在名册这一行；家已退役，不再建目录）")
    print(f"Routing:  ✅ ADR-0011 green (per-role model routing)")
    print(f"Start:    {result['command']}")
    print()
