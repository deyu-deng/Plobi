# Plobi Agent — Development Guide

Rules for agents and humans working inside this repo. Project-level truth lives
outside the repo in `../Docs/` (see **Documentation Discipline**); this file is
the repo-local operating manual.

**What this repo is.** Plobi is a personal AI agent running one agent core
across a CLI, a messaging gateway, a TUI, and an Electron desktop app. It
learns across sessions (memory + skills), delegates to subagents, runs scheduled
jobs, and drives a real terminal and browser. Capability lives at the edges
(plugins, skills, MCP servers), not in the core.

**Two things this repo is not.** It is single-owner — no external contributions,
no PR triage, no contributor credit, no upstream sync (ruling R-043). The
upstream open-source playbook (PR review flow, Discord, GitHub Actions gates,
releases) does not apply here and is not repeated in this file.

---

## 0. The rules that bite

1. **Per-conversation prompt caching is sacred.** A long-lived conversation
   reuses a cached prefix every turn. Anything that mutates past context, swaps
   toolsets, or rebuilds the system prompt mid-conversation invalidates that
   cache and multiplies the user's cost. The only exception is context
   compression. Commands that mutate system-prompt state (skills, tools,
   memory) must default to deferred invalidation (effective next session),
   with an opt-in `--now` for immediate effect.
2. **The core is a narrow waist; capability lives at the edges.** Every model
   tool ships on every API call, so the bar for a new *core* tool is high.
3. **Behavior contracts, not change-detector tests.** Assert how two pieces of
   data must relate, never a snapshot of today's data (model catalogs, config
   version literals, enumeration counts).
4. **E2E validation, not green unit mocks.** For resolution chains, config
   propagation, security boundaries, or real I/O: exercise the real path with
   real imports against a temp `PLOBI_HOME`.
5. **Extend, don't duplicate.** Before adding a module or hook, check whether
   existing infrastructure already covers the case.
6. **No speculative infrastructure.** A hook without a concrete consumer is
   speculative; removing it after plugins depend on it is not.
7. **Never wire dead code.** Unused code that was never shipped was dead for a
   reason. Before wiring an unused module into a live path, E2E-test it.
8. **Don't fabricate a problem.** Read the implementation before calling
   something a bug: check `git log -p -S` for the original intent, and
   consider that the absence of an omission may be deliberate.
9. **Don't type an identifier from memory.** Any commit SHA, package digest, or
   version literal must be produced by a command in the same session and pasted
   — never recalled. Then re-read the written value once against the source.

### Footprint ladder (new capability decision)

Highest acceptable rung first:

1. Extend existing code — zero new surface.
2. CLI command + skill — no model-tool footprint.
3. Service-gated tool (`check_fn`) — appears only when a prerequisite exists.
4. Plugin — own directory, discovered at runtime.
5. MCP server in the catalog — for structured capability that needs a tool.
6. New core tool — last resort; tool schemas ship on every call.

---

## 1. Development environment

**Windows (this machine).** Python lives in the repo venv: `.venv\Scripts\python.exe`
(`source .venv/bin/activate` does not exist here). npm goes through the mirror:
`npm_config_registry=https://registry.npmmirror.com` (direct npmjs is blocked
by the local TLS proxy).

**POSIX.** `source .venv/bin/activate`.

**Never write literal POSIX paths in code or tests.** On this machine Python
resolves `/tmp` and `/proc` against the current drive (`D:\tmp`), and a literal
`c:\...` inside Git Bash is *not* absolute — it lands under the current drive
root. Use `tempfile.gettempdir()`, `pathlib`, or env vars; assume a path that
"happens to be unwritable on Linux" is not a test premise.

**Never leave runtime data on C:** use `get_plobi_home()` (profile-aware),
never `Path.home() / ".plobi"` hardcoded. Scratch files go to `D:\Temp`.

**Tests.** The single entry point is `scripts/run_tests.sh`; direct `pytest`
calls are forbidden (the wrapper unsets credential vars, forces UTC/C.UTF-8,
and creates a throwaway `HOME`/`PLOBI_HOME`). This machine has a registered red
baseline for POSIX-shaped tests — `Code/scripts/plobi/test_baseline_windows.json`
(protocol: `Docs/runbooks/environment.md`). Re-measure before and after a
change with `python scripts/plobi/check_test_baseline.py --from-log <log>` and
only count newly red files. Never write into the real `~/.plobi/` from a test.

---

## 2. Project structure

The tree moves; the filesystem is the truth. `scripts/repo_map.md` is the
generated map of modules and their fan-in/fan-out (recomputed by
`scripts/build_repo_map.py`; a stale map is a red gate). Entry points worth
knowing:

| Path | What lives there |
|---|---|
| `run_agent.py` / `agent/` | Agent core: providers, prompt assembly, memory, caching, compression |
| `model_tools.py` / `toolsets.py` | Tool registry dispatch, toolset definitions |
| `tools/` | Core tool implementations (auto-discovered via `tools/registry.py`) |
| `plobi_cli/` | CLI subcommands, config, plugins loader, skin engine |
| `tui_gateway/` | Python JSON-RPC backend for the TUI |
| `gateway/` | Messaging gateway (session, run, platform adapters) |
| `plugins/` | Bundled plugins (platforms, memory providers, model providers, kanban…) |
| `apps/desktop/` | Electron + React renderer; `apps/shared/` is the transport package |
| `cron/` | Scheduler and job store |
| `ui-tui/` | Ink (React) terminal UI |
| `tests/` | Pytest suite — one file per behavior, subprocess-isolated |

---

## 3. TypeScript style (desktop / TUI / website)

- Small nanostores over component state when state is shared or far from the
  consumer; `useStore` in components, `$atom.get()` in actions.
- Keep persistence next to the atom that owns it.
- `src/app` owns routes and pages; `src/store` owns shared atoms; `src/lib`
  owns shared pure helpers. Route roots stay thin.
- Prefer interfaces for public props; extend React primitives
  (`React.ComponentProps<'button'>`).
- Table-driven beats condition ladders when mapping ids/routes/views.
- Async UI handlers make intent explicit: `onClick={() => void save()}`.

---

## 4. Agent core

`AIAgent.__init__` takes ~60 parameters; the essential ones are `base_url`,
`api_key`, `provider`, `model`, `enabled_toolsets`, `session_id`,
`skip_context_files`, `skip_memory`. `agent.chat(message)` is the simple API;
`agent.run_conversation(user_message, system_message=..., conversation_history=...)`
is the full one.

**The loop** lives in `run_agent.py` / `agent/conversation_loop.py`: strictly
alternating roles, tools dispatched through `handle_function_call`, iteration
and budget caps, interrupt checks. Never inject a synthetic user message
mid-loop, and never send two same-role messages in a row.

**System prompt** is assembled once per session in `agent/system_prompt.py` in
three tiers — `stable` (identity/SOUL, tool guidance, skills index),
`context` (AGENTS.md / .cursorrules under the cwd), `volatile` (memory
snapshot, USER profile, timestamp) — and cached for the conversation's life.
Only context compression may rebuild it.

**Identity**: `agent/prompt_builder.py::load_soul_md()` prefers the per-agent
SOUL under `$PLOBI_HOME/souls/<agent>.md` (resolved via
`plobi/agents/registry.py::shared_soul_path_for`), falling back to a
roster-generated identity block; it must never fall back to the L1 secretary
SOUL for an L2 agent.

---

## 5. CLI, slash commands, TUI, desktop

**CLI** — `plobi_cli/cli.py` resolves commands through the central registry
(`plobi_cli/commands.py`); every consumer (CLI, gateway, Telegram menu,
autocomplete) derives from it. A new slash command needs one `CommandDef` plus
its handler; skill slash commands are injected as user messages, not into the
system prompt, to keep the cache warm.

**TUI** — Ink owns the screen, `tui_gateway/server.py` owns Python; the
dashboard embeds the real TUI over a PTY. Do not re-implement the transcript,
composer, or terminal in React.

**Desktop** (`apps/desktop/`) — its own renderer; it talks JSON-RPC to the
`plobi serve` backend. Two load-bearing rules:
- Slash-command curation lives in `src/lib/desktop-slash-commands.ts`:
  `isDesktopSlashCommand` gates execution, `isDesktopSlashSuggestion` gates
  discovery. Non-built-ins (skills, quick commands) must keep flowing through
  both — they are user-activated extensions.
- The desktop never grows a second chat surface, and never re-implements the
  TUI.

---

## 6. Tools

Adding a core tool (last resort per the footprint ladder):

```python
# tools/your_tool.py
from tools.registry import registry

def your_tool(param: str, task_id: str = None) -> str:
    return json.dumps({"success": True, "data": param})

registry.register(
    name="your_tool",
    toolset="your_toolset",
    schema={"name": "your_tool", "description": "...", "parameters": {...}},
    handler=lambda args, **kw: your_tool(param=args.get("param", ""), task_id=kw.get("task_id")),
    check_fn=lambda: True,
)
```

Then add the name to a toolset in `toolsets.py` — auto-discovery registers the
schema, but the tool only reaches an agent if a toolset lists it. Handlers must
return a JSON string. Tool schema descriptions must not name tools from other
toolsets (they may be unavailable, which invites hallucinated calls).

**State files** go under `get_plobi_home()` (profile-aware), never
`~/.plobi` literals. `agent/memory_provider.py` / `agent/memory_manager.py`
orchestrate memory backends; `plugins/memory/` is a closed set — new memory
backends ship as standalone plugins.

**Toolsets** are defined in `toolsets.py` (the live list, not this file).
`delegate_task` spawns subagents: `role="leaf"` (no delegation, no
clarify/memory/send_message) and `role="orchestrator"`; `max_spawn_depth`
defaults to 1 (flat L1→L2 by default). The `plobi-delegation-guard` plugin
adds a process-wide ceiling, a lease TTL, and a quota breaker, gating on
`pre_tool_call` (not `subagent_start`, which would deadlock).

---

## 7. Configuration

- `config.yaml` — all behavior. `.env` — **secrets only** (API keys, tokens,
  passwords). Never introduce a new `PLOBI_*` env var for non-secret config.
- Three loaders: `load_cli_config()` (CLI), `load_config()`
  (`plobi_cli/config.py`, most subcommands), raw YAML (gateway runtime). New
  keys belong in `DEFAULT_CONFIG`; bump `_config_version` only for migrations.
- cwd: CLI uses the process cwd; messaging uses `terminal.cwd` from
  `config.yaml` (bridged to `TERMINAL_CWD` for children).
- Skins (`plobi_cli/skin_engine.py`) are pure data — customize via
  `~/.plobi/skins/*.yaml`, not code.

---

## 8. Plugins

- Discovery: `plobi_cli/plugins.py` scans `~/.plobi/plugins/`, `./.plobi/plugins/`,
  and entry points; hooks (`pre_tool_call`, `post_tool_call`, `pre_llm_call`,
  `on_session_start`, …) and `ctx.register_tool` / `ctx.register_cli_command`
  are the extension surface.
- Plugins must not modify core files; if a capability is missing, extend the
  generic plugin surface instead of hardcoding.
- Third-party product integrations do not live in this tree — ship them as
  standalone plugin repos.
- Memory backends and model providers are separate discovery systems
  (`plugins/memory/`, `plugins/model-providers/`); the latter is lazy.

---

## 9. Skills

`skills/` ships built-ins; `optional-skills/` ships heavy/niche skills that are
installed explicitly. A skill needs `name` + `description` in frontmatter;
`description` is one sentence ≤ 60 characters. Prose must reference native
tools by name (`terminal`, `read_file`, `patch`, `search_files`,
`delegate_task`), not shell utilities. Scripts live in `scripts/`, references in
`references/`, templates in `templates/`. Tests live at
`tests/skills/test_<skill>_skill.py` (stdlib + pytest + mocks only).

The curator (`agent/curator.py`) archives stale agent-created skills; it never
deletes and never touches bundled skills.

---

## 10. Profiles and identities

One shared home (`~/.plobi/`): skills, plugins, config, cron, sessions. An L2
agent is a *row* in the roster (`$PLOBI_HOME/plobi/projects.yaml` → roster,
`plobi/agents/registry.py`), not a copied directory: it carries at most an
identity fragment, a model, a secret, a mind subtree, and its own few files
(SOUL under `souls/`, memory under `memory/agents/<slug>/`).

Profile-safe code rules:
- Use `get_plobi_home()` for all state paths, `display_plobi_home()` in
  user-facing messages.
- Module-level constants are fine (they resolve after the profile override is
  applied).
- Tests that mock `Path.home()` must also set `PLOBI_HOME`.

---

## 11. Known pitfalls

- **Do not hardcode `~/.plobi`** in code or tests.
- **Do not use `simple_term_menu`** — it has ghost-duplication rendering bugs;
  use `plobi_cli/curses_ui.py`.
- **Do not emit `\033[K`** (ANSI erase-to-EOL) — it leaks as literal text under
  prompt_toolkit's `patch_stdout`.
- **`_last_resolved_tool_names` is a process-global** in `model_tools.py`; it
  may be stale during child-agent runs.
- **The gateway has two message guards** (base adapter + gateway runner); any
  new command that must reach a running agent has to bypass both and dispatch
  inline.
- **Do not wire in dead code** without E2E proof.
- **Tests must not write to the real `~/.plobi/`** — the wrapper's isolation is
  the mechanism.

---

## 12. Testing

```bash
scripts/run_tests.sh                      # full suite, CI parity
scripts/run_tests.sh tests/agent/         # one directory
scripts/run_tests.sh tests/agent/test_x.py::test_y
scripts/run_tests.sh -v --tb=long         # extra pytest flags
```

Each test file runs in a fresh subprocess (`run_tests_parallel.py`), so module
state cannot leak between files. Write behavior tests: a test that fails
whenever today's data changes (catalog snapshots, config versions, counts) is a
change-detector test — delete it or convert it into an invariant.

---

## 13. Documentation discipline

- **Project-level truth is outside this repo**: `../Docs/` holds requirements,
  specs, rulings, runbooks, progress. The index is `../Docs/README.md`.
  Do not create a second doc tree inside `Code/docs/`.
- New documents start from `../Docs/templates/*.md` and get an index row.
- Outdated documents are absorbed, then deleted — no "keep it just in case"
  copies.
- One-off task briefs live in `../Docs/tasks/` and are deleted on acceptance.
