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
//   2. DOWNGRADE A FAILURE, NOT THE CHECK. A failed probe becomes `deferred`
//      with the observed cause attached; it is never elided and never recoloured
//      green.
//   3. LIGHT UP THE MOMENT IT IS ALIVE. If the port answers, the row is `ready`
//      — deferral is a priority decision, not a permanent disable.
//
// The ledger is pure state (injectable clock) so it can be unit-tested without
// Electron, and so the main process stays the single source of truth: the tray
// menu and the boot-progress payload are two renderings of the SAME rows, never
// two separate status systems.
// ─────────────────────────────────────────────────────────────────────────────

export type DeferredSubsystemState = 'deferred' | 'probing' | 'ready'

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
  /** Ready: what was observed. Deferred: observed cause + R-047 wording. */
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
    name: { en: 'aigw quota gateway', zh: '额度网关 aigw' },
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

function definitionFor(id: string): DeferredSubsystemDefinition | null {
  return DEFERRED_SUBSYSTEMS[id] ?? null
}

/**
 * The user-facing reason line for a row. `ready` rows must never carry the
 * deferred suffix, and a `deferred` row must always carry what was actually
 * observed — an empty observation would make "not installed" indistinguishable
 * from "not checked".
 */
function deferredDetail(row: Omit<DeferredSubsystemRow, 'detail'>): string {
  if (row.state === 'ready') {
    return row.observed
  }

  if (!row.observed) {
    return `${DEFERRED_SUFFIX}. Enable: ${row.enableHint}`
  }

  return `${row.observed} — ${DEFERRED_SUFFIX}. Enable: ${row.enableHint}`
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
     * not part of the deferred tier must not silently acquire a row).
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

export { createDeferredSubsystemLedger, DEFERRED_SUBSYSTEMS, DEFERRED_SUFFIX, deferredDetail, definitionFor }
