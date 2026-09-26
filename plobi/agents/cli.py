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
    return AgentEntry(
        name=name,
        role=getattr(args, "role", None) or "l2_project",
        profile=getattr(args, "profile", None) or "",
        provider=getattr(args, "provider", None) or "",
        model=getattr(args, "model", None) or "",
        mind_subtree=getattr(args, "mind_subtree", None) or "",
        skills=tuple(getattr(args, "skills", None) or ()),
        description=getattr(args, "description", None) or "",
    )


def run_plobi(args: Any) -> None:
    action = getattr(args, "plobi_action", None)
    if action is None:
        print("usage: plobi plobi <agents> ...")
        print("  agents list              list resident L2 agents")
        print("  agents register NAME     add/update a registry entry")
        print("  agents spawn NAME        materialize profile + model routing")
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
    print(f"{'Agent':<16} {'Role':<14} {'Profile':<16} {'Model':<24} Routing")
    print(f"{'─'*16} {'─'*14} {'─'*16} {'─'*24} {'─'*9}")

    for entry in registry.entries():
        try:
            route = registry.router().resolve(entry.role)
        except Exception:
            route = None
        model = route.qualified if route else "—"
        marker = "✅" if problems == [] else "⚠️"
        print(
            f"{entry.name:<16} {entry.role:<14} {entry.profile_name:<16} "
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
    print(f"Registered {name} (role={entry.role}, profile={entry.profile_name})")
    print(f"Registry: {registry.path}")
    print(f"Materialize: plobi plobi agents spawn {name}")


def _spawn(registry: AgentRegistry, args: Any) -> None:
    name = getattr(args, "name", None)
    if not name:
        print("agents spawn requires a NAME", file=sys.stderr)
        sys.exit(1)

    # 允许 spawn 时用 --role 等直接注册（不必先 register）。
    if registry.get(name) is None and any(
        getattr(args, f, None) for f in ("role", "profile", "provider", "model")
    ):
        registry.upsert(_entry_from_args(name, args))
        registry.save()

    clone_from = getattr(args, "clone_from", None)
    try:
        result = registry.spawn(name, clone_from=clone_from)
    except RegistryError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"\nSpawned {result['name']} ({result['role']})")
    print(f"Profile:  {result['profile']}  → {result['profile_dir']}")
    print(f"Routing:  ✅ ADR-0011 green (L1 ≠ L2 model)")
    print(f"Start:    {result['command']}")
    print()
