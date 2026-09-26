"""§5 console data API — the read surface behind the L1/L2 three-pane console.

Nothing here invents agent machinery: the registry (B2), the subagent
tracker (B4) and the model router (ADR-0011) already own that. Per
ARCH-UI-MASTER §3.5 the whole surface lives in ONE module:
:mod:`plobi.console.router` (HTTP + row assembly + usage aggregation).

Contract: ``docs/specs/ui-l1-console-spec.md`` §5 — every response is the
envelope ``{ ok: bool, data | error }`` and the row shapes mirror
``apps/desktop/src/app/console/types.ts`` exactly (``Agent``,
``AgentOverview``, ``AgentSubagent``). Neither file is edited from here: when
they disagree, stop and escalate (ARCH-AGENT-COORDINATION §7).

Note: this package deliberately does NOT re-export ``router`` — that would
shadow the ``plobi.console.router`` module attribute and break
``import plobi.console.router`` for callers/tests. Import from the module.
"""
