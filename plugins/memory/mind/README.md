# Mind memory provider (Plobi plugin)

**Status: P0 IMPLEMENTED (2026-08-29).** Structure, compliance boundaries, and all lifecycle methods are in place: keyword retrieval, per-turn note export, session digest, and memory mirror all write through `plobi.mind.writer.MindWriter` under `_is_safe` guards.

## What this is

A Plobi `MemoryProvider` plugin that bridges the agent to your
[Mind](file:///D:/Projects/Plobi/Code/mind) second-brain vault (Obsidian markdown,
file-backed). It is the native-adaptation path discussed in
`Docs/specs/memory-adapter.md`.

## Architecture fit

- Plobi memory is **provider-pluginized** — adding this folder is the *only*
  integration step. No changes to `agent/memory_provider.py`,
  `agent/memory_manager.py`, or `run_agent.py`.
- The loader (`plugins/memory/__init__.py`) discovers this plugin dynamically
  by scanning `plugins/memory/<name>/` and instantiates it via `register()`.
- Activation is pure config: set `memory.provider: mind` in `config.yaml`
  (or via `plobi memory setup`).

## Compliance boundary (read before implementing)

Mind has a git pre-commit verifier (`Loom/scripts/verifier.py`) that
**BLOCKS** commits when:

1. `Vault/projects` top-level directory names ≠ `AGENTS.md §1` declaration, or
2. `Loom/skills` skill count ≠ `AGENTS.md` declaration.

All real writes must stay inside `SAFE_PREFIXES` (defined in `mind.py`):
`Vault/projects/Plobi/` (capital V — the official vault's current Plobi fork
Plobi project dir; the lowercase `plobi` dir is a legacy Plobi archive, do
not write there), `Vault/{meta,notes,journal,inbox}`,
`Loom/wiki/{concepts,entities,sources,comparisons}`,
`Loom/raw/chat-logs/{exports,digested}`.

Mind conventions: kebab-case filenames; no AI meta-comments in `Vault/`;
no `Vault → Loom` wikilinks.

## Files

| File | Purpose |
|------|---------|
| `plugin.yaml` | Plugin metadata + declared hooks |
| `__init__.py` | Entrypoint; registers `MindProvider` with the loader |
| `mind.py` | `MindProvider(MemoryProvider)` — all lifecycle methods, implemented |
| `retrieval.py` | Retrieval helper (keyword/FTS-style grep over SAFE_PREFIXES), implemented |

## Activation

```yaml
# config.yaml (PLOBI_HOME, e.g. D:\Data\AppData\Plobi\config.yaml)
memory:
  provider: mind
```

Optional env override: `MIND_ROOT=/path/to/Mind` (not required — without it
`resolve_root()` resolves the sibling `Code/mind` → `D:/Projects/Plobi/Code/mind`).

## Verification (implemented)

Run Mind's verifier to confirm no knowledge-base pollution:

```bash
cd D:/Projects/Plobi/Code/mind && python Loom/scripts/verifier.py --strict
# expect exit code 0 (no BLOCKER, no new WARN)
```
