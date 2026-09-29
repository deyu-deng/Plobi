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
//
// ── Profile backends (framelet crash-loop, 2026-09-29) ───────────────────────
// Rule 2 above was enforced for the two sidecars and for the primary backend
// (`backendStartFailure` in main.ts latches a failed boot), but NOT for a
// per-profile pool backend: a profile whose directory had never been
// materialised exited 1, its pool entry was deleted, and the very next renderer
// request spawned it again — 1173 identical Start/Error/exit cycles in
// `~/.plobi/logs/desktop.log` while the panel stayed on "checking". That is a
// 真故障 that never reached `red`, which is exactly the state ac05264 was added
// to make visible. Two things live here for it now:
//
//   * a lazily-created `backend:<profile>` row, so a crash-looping profile
//     backend reports through the SAME four states and the SAME two renderings
//     as the sidecars — no fifth state, no parallel status store;
//   * `createBackendRespawnGuard()`, the exponential backoff the spawn path was
//     missing. It is pure clock state (arm / blockReason / clear) so the retry
//     interval is testable without Electron, and it deliberately keeps the
//     failure *recoverable*: the gate only blocks until the next attempt is due,
//     so the profile coming back to life — or the user asking again — gets
//     through on its own.
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
   *
   * For the quota hub this is a *statement*, not a command line: 裁定 (2026-09-29)
   * 「改成不可执行的说明，由软件自行拉起」 — the app brings it up itself, so the
   * panel must not hand the user a shell command to run.
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
    enableHint: 'Plobi starts this service itself when it is needed — there is nothing to run by hand.',
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

/**
 * Row ids for per-profile Plobi backends, spawned by `spawnPoolBackend()` in
 * main.ts. They share this ledger (and therefore `red`) with the sidecars, but
 * they are NOT part of `DEFERRED_SUBSYSTEMS`: which profiles get attempted is
 * only known at runtime, and they belong to no R-047 deferral tier.
 */
const PROFILE_BACKEND_ID_PREFIX = 'backend:'

/** Same shape as the quota hub hint: the app owns the lifecycle, nothing to run. */
const PROFILE_BACKEND_ENABLE_HINT =
  'Plobi brings this profile backend up itself and backs off between attempts — there is nothing to run by hand.'

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
  return DEFERRED_SUBSYSTEMS[id] ?? profileBackendDefinition(id)
}

/**
 * A profile backend row, synthesised from its id so main.ts never has to keep a
 * second definition table. `port` is 0 while the backend never got to announce
 * one — the renderers omit a non-positive port rather than printing "(:0)".
 */
function profileBackendDefinition(id: string): DeferredSubsystemDefinition | null {
  if (!id.startsWith(PROFILE_BACKEND_ID_PREFIX)) {
    return null
  }

  const profile = id.slice(PROFILE_BACKEND_ID_PREFIX.length).trim()

  if (!profile) {
    return null
  }

  return {
    enableHint: PROFILE_BACKEND_ENABLE_HINT,
    id,
    name: { en: `Profile backend · ${profile}`, zh: `项目后端 · ${profile}` },
    port: 0
  }
}

/** The ledger id a profile backend reports under. */
function profileBackendRowId(profile: string): string {
  return `${PROFILE_BACKEND_ID_PREFIX}${String(profile).trim()}`
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
     *
     * A `backend:<profile>` row is created by its first observation, since the
     * set of profiles this machine attempts is runtime data. Any other unknown
     * id is still refused.
     */
    record(
      id: string,
      { observed, state }: { observed: string; state: DeferredSubsystemState }
    ): DeferredSubsystemRow | null {
      const definition = definitionFor(id)

      if (!definition || (!rows.has(id) && !id.startsWith(PROFILE_BACKEND_ID_PREFIX))) {
        return null
      }

      return commit({
        ...definition,
        observed: String(observed || '').trim() || 'no observation recorded',
        state,
        timestamp: now()
      })
    },

    /**
     * Drop a row we no longer intend to attempt (a profile backend the user
     * deleted or the pool reaped). Returns whether anything was removed, so the
     * caller only re-renders when the ledger actually changed.
     */
    forget(id: string): boolean {
      return rows.delete(id)
    }
  }
}

// ── Respawn backoff ─────────────────────────────────────────────────────────
// The pool backend path had none: `ensureBackend()` deleted the failed entry and
// the next caller spawned again, so a profile that exits 1 on startup was
// respawned as fast as the renderer asked (a 15 s status poll plus every REST
// call). Exponential, capped, and *not* a permanent stop — see the module header.

const RESPAWN_BASE_DELAY_MS = 2_000
const RESPAWN_MAX_DELAY_MS = 120_000

/**
 * Attempts after which we stop *scheduling* retries on our own. The gate still
 * expires, so the next user action re-attempts — "bounded retry" rather than a
 * latch that outlives the fix.
 */
const RESPAWN_MAX_AUTO_RETRIES = 8

interface RespawnGate {
  attempt: number
  message: string
  nextAttemptAt: number
}

/**
 * Pure exponential backoff keyed by profile, with an injectable clock.
 * `arm()` is the only place the interval is computed, which is what makes the
 * "does not retry immediately, and the gap grows" behaviour unit-testable.
 */
function createBackendRespawnGuard({
  baseDelayMs = RESPAWN_BASE_DELAY_MS,
  maxAutoRetries = RESPAWN_MAX_AUTO_RETRIES,
  maxDelayMs = RESPAWN_MAX_DELAY_MS,
  now = () => Date.now()
}: {
  baseDelayMs?: number
  maxAutoRetries?: number
  maxDelayMs?: number
  now?: () => number
} = {}) {
  const gates = new Map<string, RespawnGate>()

  return {
    /**
     * Record one more failed attempt and return the schedule for the next one.
     * `autoRetry` says whether the caller should arm a timer at all.
     */
    arm(profile: string, message: string): { attempt: number; autoRetry: boolean; delayMs: number } {
      const attempt = (gates.get(profile)?.attempt ?? 0) + 1
      const delayMs = Math.min(maxDelayMs, baseDelayMs * 2 ** (attempt - 1))

      gates.set(profile, { attempt, message, nextAttemptAt: now() + delayMs })

      return { attempt, autoRetry: attempt <= maxAutoRetries, delayMs }
    },

    /**
     * Why this profile may not be spawned right now, or `null` when a spawn is
     * allowed. The message carries the previous failure verbatim so a caller who
     * is blocked still learns the cause instead of getting a bare refusal.
     */
    blockReason(profile: string): string | null {
      const gate = gates.get(profile)

      if (!gate || now() >= gate.nextAttemptAt) {
        return null
      }

      const waitSeconds = Math.ceil((gate.nextAttemptAt - now()) / 1000)

      return (
        `Plobi backend for profile "${profile}" failed (attempt ${gate.attempt}; ` +
        `next try in ${waitSeconds}s): ${gate.message}`
      )
    },

    /** True when a gate existed — so the caller only re-renders on a real change. */
    clear(profile: string): boolean {
      return gates.delete(profile)
    }
  }
}

export {
  createBackendRespawnGuard,
  createDeferredSubsystemLedger,
  DEFERRED_SUBSYSTEMS,
  DEFERRED_SUFFIX,
  PROFILE_BACKEND_ENABLE_HINT,
  PROFILE_BACKEND_ID_PREFIX,
  RED_SUFFIX,
  RESPAWN_BASE_DELAY_MS,
  RESPAWN_MAX_AUTO_RETRIES,
  RESPAWN_MAX_DELAY_MS,
  deferredDetail,
  definitionFor,
  profileBackendRowId
}
