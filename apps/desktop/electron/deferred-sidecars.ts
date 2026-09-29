// ─────────────────────────────────────────────────────────────────────────────
// R-051 — the honest status ledger for the two subsystems R-047 pushed to the
// last tier: the aigw quota gateway (:8000) and the chatlog WeChat collector
// (:5030).
//
// Product context: 「先不要做采集的功能了……等应用打磨得比较完整了，我才会提供数据的」
// (R-047). Their failure at boot is therefore NOT a fault — but reporting them
// as *success* is a false state, and this project has already been burned once
// by documentation that looked like everything ran. The Python side solved the
// same problem in `scripts/plobi/doctor.py` (commit 4b437be) with a fourth
// status, `deferred`. This module is the desktop-side mirror of that decision,
// with the same three rules:
//
//   1. PROBE ANYWAY. A row is only ever written from a real observation.
//   2. DOWNGRADE A DEFERRAL, NEVER A FAULT. "Nothing on this machine could even
//      be started" (no binary, no config) is `deferred` with the observed cause
//      attached. "It WAS attempted and it did not come up healthy" — the port
//      answered non-200, the spawn threw, the config exists yet the gateway
//      stayed unhealthy — is `red`, the same call doctor.py makes at its
//      `reachable but non-200 = RED` branch (scripts/plobi/doctor.py:439-443,
//      :457-462). Folding a real fault into `deferred` is what produced a yellow
//      warning card on every clean boot and hid the ones that mattered.
//   3. LIGHT UP THE MOMENT IT IS ALIVE. If the port answers, the row is `ready`
//      — deferral is a priority decision, not a permanent disable, and a red row
//      that starts answering becomes ready on the very next probe.
//
// R-048's lesson, applied on this side of the bridge: there is no fourth state
// "broken-but-quiet". `deferred` and `red` are different states with different
// renderings, and a renderer that cannot tell them apart is lying.
//
// The ledger is pure state (injectable clock) so it can be unit-tested without
// Electron, and so the main process stays the single source of truth: the tray
// menu and the boot-progress payload are two renderings of the SAME rows, never
// two separate status systems.
// ─────────────────────────────────────────────────────────────────────────────

export type DeferredSubsystemState = 'deferred' | 'probing' | 'ready' | 'red'

export interface DeferredSubsystemName {
  en: string
  zh: string
}

export interface DeferredSubsystemDefinition {
  id: string
  name: DeferredSubsystemName
  port: number
  /**
   * What it would take to bring this subsystem up *on this machine*. Mirrors
   * `DEFERRED_ENABLE_HINTS` in scripts/plobi/doctor.py — keep the two in sync;
   * neither may name a Windows-only artifact, since the Windows box was
   * decommissioned on 2026-09-27.
   */
  enableHint: string
}

export interface DeferredSubsystemRow extends DeferredSubsystemDefinition {
  /** Ready: what was observed. Deferred: observed cause + R-047 wording. Red: observed cause + fault wording. */
  detail: string
  observed: string
  state: DeferredSubsystemState
  timestamp: number
}

const DEFERRED_SUBSYSTEMS: Record<string, DeferredSubsystemDefinition> = {
  aigw: {
    enableHint:
      'cd aigw && uv run python -m aigw start --config config.yaml (port 8000 = config.yaml server.port), then move routing.rules off the mock provider',
    id: 'aigw',
    name: { en: 'Local Quota Hub', zh: '本地额度网关' },
    port: 8000
  },
  chatlog: {
    enableHint:
      'install Go, cd tools/chatlog && go build -o bin/chatlog ./cmd/chatlog, log into WeChat, export CHATLOG_DATA_KEY, then serve on :5030',
    id: 'chatlog',
    name: { en: 'chatlog WeChat collector', zh: '微信采集 chatlog' },
    port: 5030
  }
}

/** R-047's wording, kept identical to doctor.py's `deferred_row` detail. */
const DEFERRED_SUFFIX = 'deferred by R-047 (postponed, not a fault; turns green on its own once live)'

/**
 * The fault wording for `red`. It deliberately carries NONE of the R-047
 * "postponed, not a fault" sentence — that suffix is precisely what made a real
 * failure read as an intentional deferral, and the byte-drift guard in
 * `deferred-sidecars.test.ts` pins only the `deferred` branch to doctor.py.
 * Wording must stay honest; the byte guard stays where it is.
 */
const RED_SUFFIX = 'RED — real fault, not a deferral (it was attempted here and never came up healthy)'

function definitionFor(id: string): DeferredSubsystemDefinition | null {
  return DEFERRED_SUBSYSTEMS[id] ?? null
}

/**
 * The user-facing reason line for a row. `ready` rows must never carry the
 * deferred suffix, and a `deferred` row must always carry what was actually
 * observed — an empty observation would make "not installed" indistinguishable
 * from "not checked".
 *
 * `red` gets the same "what was observed + how to enable" shape, but with the
 * fault suffix instead of the deferral suffix, so no renderer can mistake an
 * attempted-and-broken service for a postponed one.
 */
function deferredDetail(row: Omit<DeferredSubsystemRow, 'detail'>): string {
  if (row.state === 'ready') {
    return row.observed
  }

  const suffix = row.state === 'red' ? RED_SUFFIX : DEFERRED_SUFFIX

  if (!row.observed) {
    return `${suffix}. Enable: ${row.enableHint}`
  }

  return `${row.observed} — ${suffix}. Enable: ${row.enableHint}`
}

/**
 * Create the ledger. `ids` defaults to the two R-047 subsystems; a row always
 * exists for every id so the UI can say "still probing" instead of going
 * silent — silence is what this module exists to remove.
 */
function createDeferredSubsystemLedger({
  ids = Object.keys(DEFERRED_SUBSYSTEMS),
  now = () => Date.now()
}: { ids?: string[]; now?: () => number } = {}) {
  const rows = new Map<string, DeferredSubsystemRow>()

  const commit = (observation: Omit<DeferredSubsystemRow, 'detail'>): DeferredSubsystemRow => {
    const row: DeferredSubsystemRow = { ...observation, detail: deferredDetail(observation) }

    rows.set(row.id, row)

    return row
  }

  for (const id of ids) {
    const definition = definitionFor(id)

    if (!definition) {
      continue
    }

    commit({ ...definition, observed: 'probe queued', state: 'probing', timestamp: now() })
  }

  return {
    /** Current rows, ordered by id for a stable rendering. */
    rows(): DeferredSubsystemRow[] {
      return [...rows.values()].sort((left, right) => left.id.localeCompare(right.id)).map(row => ({ ...row }))
    },

    /**
     * Record one real observation. Unknown ids are ignored (a sidecar that is
     * not part of the deferred tier must not silently acquire a row). Accepts
     * every state in `DeferredSubsystemState`, `red` included — the ledger
     * physically cannot swallow a real fault into `deferred`.
     */
    record(
      id: string,
      { observed, state }: { observed: string; state: DeferredSubsystemState }
    ): DeferredSubsystemRow | null {
      const definition = definitionFor(id)

      if (!definition || !rows.has(id)) {
        return null
      }

      return commit({
        ...definition,
        observed: String(observed || '').trim() || 'no observation recorded',
        state,
        timestamp: now()
      })
    }
  }
}

export { createDeferredSubsystemLedger, DEFERRED_SUBSYSTEMS, DEFERRED_SUFFIX, RED_SUFFIX, deferredDetail, definitionFor }
