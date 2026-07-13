# Mind memory provider (Vaelis plugin)

**Status: FRAMEWORK SCAFFOLD ONLY.** Structure and compliance boundaries are
in place; no real disk I/O is implemented yet. Review, then implement.

## What this is

A Vaelis `MemoryProvider` plugin that bridges the agent to your
[Mind](file:///Users/ciel/Mind) second-brain vault (Obsidian markdown,
file-backed). It is the native-adaptation path discussed in
`docs/MIND_ADAPTER_PLAN.md`.

## Architecture fit

- Vaelis memory is **provider-pluginized** — adding this folder is the *only*
  integration step. No changes to `agent/memory_provider.py`,
  `agent/memory_manager.py`, or `run_agent.py`.
- The loader (`plugins/memory/__init__.py`) discovers this plugin dynamically
  by scanning `plugins/memory/<name>/` and instantiates it via `register()`.
- Activation is pure config: set `memory.provider: mind` in `config.yaml`
  (or via `hermes memory setup`).

## Compliance boundary (read before implementing)

Mind has a git pre-commit verifier (`Mind/Loom/scripts/verifier.py`) that
**BLOCKS** commits when:

1. `Vault/projects` top-level directory names ≠ `AGENTS.md §1` declaration, or
2. `Loom/skills` skill count ≠ `AGENTS.md` declaration.

All real writes must stay inside `SAFE_PREFIXES` (defined in `mind.py`):
`Vault/projects/vaelis/`, `Vault/{meta,notes,journal,inbox}`,
`Loom/wiki/{concepts,entities,sources,comparisons}`,
`Loom/raw/chat-logs/{exports,digested}`.

Mind conventions: kebab-case filenames; no AI meta-comments in `Vault/`;
no `Vault → Loom` wikilinks.

## Files

| File | Purpose |
|------|---------|
| `plugin.yaml` | Plugin metadata + declared hooks |
| `__init__.py` | Entrypoint; registers `MindProvider` with the loader |
| `mind.py` | `MindProvider(MemoryProvider)` — all lifecycle methods, stubbed |
| `retrieval.py` | Retrieval helper (keyword/semantic search), stubbed |

## Activation (when implemented)

```yaml
# config.yaml
memory:
  provider: mind
```

Optional env override: `MIND_ROOT=/path/to/Mind` (defaults to
`/Users/ciel/Mind` on macOS, `D:\Mind` on Windows).

## Verification (when implemented)

After implementation, run Mind's verifier to confirm no knowledge-base
pollution:

```bash
cd /Users/ciel/Mind && python3 Loom/scripts/verifier.py --strict
# expect exit code 0 (no BLOCKER, no new WARN)
```
