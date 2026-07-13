# VAELIS_PATCHES.md

Tracking document for deviations made to the upstream **Hermes Agent** monorepo
(frozen at tag `hermes-upstream` = commit `4281151`) on the `vaelis-dev` branch.

Vaelis is a **superset** of Hermes Agent. The entire Hermes monorepo now lives at
the repository root and is "the foundation"; our previously-written Vaelis code is
archived under `backup/`. Goal: rebrand from **Hermes** → **Vaelis** while keeping
original code traceable and functionally intact. Other logic changes (aigw quota
seam, etc.) are deferred.

## Repository layout (after restructure)

- `/` (repo root) = the Hermes monorepo, de-branded, on `vaelis-dev`.
  - `apps/desktop/` = the Electron desktop shell (the immediate launch target).
  - `apps/shared/` = `@hermes/shared` workspace package.
  - `agent/`, `gateway/`, `hermes_cli/`, `tools/`, `ui-tui/`, `web/`, `website/`, …
    = the rest of the Hermes foundation (backend, TUI, docs site, etc.).
- `backup/` = our pre-fork Vaelis code + old root `.git` (`3bab334`, branch `main`).
  Old history is preserved there; this folder is intentionally **untracked**.
- `.workbuddy/` = agent tooling (untracked, not part of the product).

## De-branding status: COMPLETE (user-facing surfaces, whole monorepo)

Case-sensitive whole-word `\bHermes\b` → `Vaelis` replacement, applied tree-wide
(excludes `node_modules`, `backup`, `.git`, `.workbuddy`, `dist`, and this file +
`NOTICE` for attribution accuracy):

- **Whole-monorepo pass (this restructure):** **10,222** replacements across
  **1,712** files (README/docs, `ui-tui`, `packaging/homebrew`, `hermes_*.py`,
  `tools/`, `docker/SOUL.md`, `nix/`, `cli.py`, `run_agent.py`, etc.).
- Earlier desktop-specific passes (before restructure): 524 + 249 replacements
  across `apps/desktop` (src/i18n, electron/main, tests, scripts, intro-copy,
  package.json, README).

The capital-word regex **automatically spares**:
- CamelCase compounds (`HermesGateway`, `HermesDesktop`) — no word boundary → untouched.
- Lowercase identifiers (`@hermes/shared`, `hermesDesktop`, `hermes_cli`, `~/.hermes`,
  CLI `hermes`, `@hermes/ink`) — these are the deferred internal identifiers below.

Desktop-specific config/identifier patches (lowercase, not caught by the capital-word pass):

- `apps/desktop/package.json`: `name` hermes→vaelis, `appId`
  com.nousresearch.hermes→com.vaelis.desktop, deep-link `schemes` ["hermes"]→["vaelis"],
  productName/executableName/CFBundle*/dmg title/nsis shortcut/win legalTrademarks/linux
  synopsis → Vaelis, protocol display name "Hermes Protocol"→"Vaelis Protocol".
- `apps/desktop/electron/main.ts`: `const HERMES_PROTOCOL`→`VAELIS_PROTOCOL`
  (value `'hermes'`→`'vaelis'`); `app.setAppUserModelId('com.nousresearch.hermes')`
  →`'com.vaelis.desktop'`; `hermes://` doc comments → `vaelis://`.
- `apps/desktop/electron/update-relaunch.test.ts`: 3 literals → `vaelis://`.
- `apps/desktop/README.md`: `tccutil reset Microphone com.nousresearch.hermes`
  →`com.vaelis.desktop`.

`author` in package.json is intentionally kept as "Nous Research" for attribution
(the original copyright holder). The fork's own copyright lives in `NOTICE`.

## KNOWN GAPS (visible brand sources outside first-party source)

1. **Third-party UI kit** — `node_modules/@nous-research/ui` (`^0.13.0`) hardcodes
   "Hermes Agent" marks in its `poster` / `overlays/lens` components. Renders in the
   running app. Requires a dependency decision (fork the kit or override components).
2. **Build artifacts** — `plugins/hermes-achievements/dashboard/dist/index.js` still
   contains "HERMES AGENT" / "Hermes Agent" (regenerable `dist/`, skipped by design).
3. **All-caps asset** — `website/static/img/docs/cli-layout.svg` has "HERMES AGENT"
   (static SVG text, not matched by the capital-word regex).

None of these affect the desktop shell's own chrome; #1 is the only one visible in-app.

## DEFERRED internal identifiers (intentionally NOT renamed)

Not user-visible brand strings; renaming risks breaking the renderer↔main IPC
contract or orphaning user data, so they stay for now:

- `window.hermesDesktop` — Electron↔renderer IPC bridge object.
- `@/hermes`, `@/types/hermes` module imports (files `src/hermes.ts`,
  `src/types/hermes.ts` + `.test.ts` siblings — content already de-branded).
- `@hermes/shared` — shared workspace package name (separate package).
- `HermesGateway` type alias re-exported from `@/hermes`.
- localStorage keys: `hermes.desktop.*`, `hermes-desktop-*`, `hermes:composer-drafts:v3`.
- `data-hermes-*` HTML attributes on `<html>`.
- IPC channel strings: `hermes:deep-link`, `hermes:deep-link-ready`,
  `hermes:vscode-theme:search`, `hermes:saveImageFromUrl`, etc. (must match main).
- Backend references in copy: `hermes_cli`, `~/.hermes`, `hermes_session_*` cookies,
  `hermes-media` custom protocol, `persist:hermes-embed` partition, gateway `/hermes`
  path prefix (describe the underlying agent backend, not the desktop shell).

## Traceability

- `upstream` remote → Nous Research original.
- `hermes-upstream` tag → pristine upstream freeze (`4281151`) — lives in this repo's tags.
- `vaelis-dev` branch (repo root `.git`) → our edits (currently `344af3f`).
- Old Vaelis history (`3bab334`, `main`) preserved in `backup/.git`.
- Quantify original-code ratio: `git diff hermes-upstream...vaelis-dev`.

## Open tasks (see task list)

- #33 wire aigw quota seam (deferred per "change other logic later").
- #34 run + verify actual render (needs real Electron / display; headless verification
  done via `npm install` + `typecheck` + `build` — see runbook below).
- #36 rebrand `@nous-research/ui` dependency (Hermes Agent marks).
- Later: reduce original-code trace below 50% before open-sourcing.

## Launch runbook (desktop)

From repo root:

1. `npm install` — installs all workspaces (root + `apps/*` + `ui-tui` + `web`).
2. `cd apps/desktop`
3. `npm run dev` — Vite renderer on `127.0.0.1:5174` + `electron .` (real desktop window).
   - Needs a display server. In a headless/CI box, `npm run build` is the verifiable proxy.
   - `npm run dev:fake-boot` boots with `HERMES_DESKTOP_BOOT_FAKE=1` (simulated boot steps).
4. `npm run build` — `tsc -b` + Vite build + bundle electron main + stage native deps
   → emits `apps/desktop/dist/`. No display required; proves the app is buildable/runnable.
5. `npm run typecheck` — `tsc -p . --noEmit && tsc -p tsconfig.electron.json --noEmit`.
