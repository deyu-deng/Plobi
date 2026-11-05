"""``plobi plobi agents list/spawn`` CLI 处理逻辑。

薄壳 ``cmd_plobi``（在 plobi_cli/main.py）把 argparse 结果转发到这里。
表格/文案保持简洁，不发明样式。
"""

from __future__ import annotations

import sys
from typing import Any, Optional

from .registry import (
    AgentEntry,
    AgentRegistry,
    RegistryError,
    default_path,
    load_registry,
)


def _entry_from_args(name: str, args: Any) -> AgentEntry:
    # ``agents register`` 这一步**就是**登记（R-072）：名册成员资格在这儿盖成
    # ``True``，和「Mind 投影不是登记」的 ``registered=False`` 是同一枚章的两面。
    #
    # 这里原来还传一格 ``profile=``：那是「一个分身一个家」时代的门牌号，概念已取消
    # （裁 178 乙案／ADR-0013），而 ``args`` 上从来没有这枚旗（``--profile`` 不在
    # register 的 parser 里）——传下去只是把退役名字再写一遍。11-05 名册把这一格改成
    # 由 ``name`` 派生的运行时 slug 之后，这一行会变成 ``TypeError`` 直接崩掉登记。
    return AgentEntry(
        name=name,
        role=getattr(args, "role", None) or "l2_project",
        provider=getattr(args, "provider", None) or "",
        model=getattr(args, "model", None) or "",
        mind_subtree=getattr(args, "mind_subtree", None) or "",
        skills=tuple(getattr(args, "skills", None) or ()),
        description=getattr(args, "description", None) or "",
        registered=True,
    )


def run_plobi(args: Any) -> None:
    action = getattr(args, "plobi_action", None)
    if action is None:
        print("usage: plobi plobi <agents> ...")
        print("  agents list              list resident L2 agents")
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
        print("Add one:  plobi plobi agents register <name> --role l2_agenda")
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
        getattr(args, f, None) for f in ("role", "provider", "model")
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
    print(f"Routing:  ✅ ADR-0011 green (L1 ≠ L2 model)")
    print(f"Start:    {result['command']}")
    print()
