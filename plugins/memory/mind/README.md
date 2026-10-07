# Mind memory provider (Plobi plugin)

**Status: P0 IMPLEMENTED (2026-08-29).** Structure, compliance boundaries, and all lifecycle methods are in place: keyword retrieval, per-turn note export, session digest, and memory mirror all write through `plobi.mind.writer.MindWriter` under `_is_safe` guards.

## What this is

A Plobi `MemoryProvider` plugin that bridges the agent to your
Mind second-brain vault (Obsidian markdown, file-backed). It is the
native-adaptation path discussed in
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

`MIND_ROOT` is how you point at the vault, and the vault does not live in this
repo — on this Windows box it sits in the data home next to `PLOBI_HOME`.
Without the env var, `resolve_root()` falls back to an in-repo `mind/`
checkout; when neither resolves, the brain is simply not configured
(`resolve_root()` returns `None`, `require_root()` raises `MindUnavailable`).

## Verification (implemented)

Run Mind's verifier to confirm no knowledge-base pollution:

```bash
cd "$MIND_ROOT" && python Loom/scripts/verifier.py --strict
# expect exit code 0 (no BLOCKER, no new WARN)
```
