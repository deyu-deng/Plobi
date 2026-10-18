// ─────────────────────────────────────────────────────────────────────────────
// R-051 — the honest status ledger for the two subsystems that a since-revoked postponement pushed to the
// last tier: the aigw quota gateway (:8000) and the chatlog WeChat collector
// (:5030).
//
// Product context: 「先不要做采集的功能了……等应用打磨得比较完整了，我才会提供数据的」
// (that ruling was revoked on 2026-09-30 by 裁定 48; the fourth state survives by 裁定 162). Their failure at boot is therefore NOT a fault — but reporting them
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
//   * inside that guard, one class of failure is *deterministic*: a profile that
//     does not exist (or a name the CLI will not accept as a profile id at all).
//     Those get no retry timer and a long hold window, because a timer on a
//     deterministic failure is just an infinite loop that happens to cost a
//     process boot each time — which is how five data-only roster rows filled
//     desktop.log with ~3,900 identical spawn cycles and stalled the backend's
//     event loop for ~20 s at a stretch (2026-10-13, WP-DESKTOP-POOL-GHOST-PROFILE).
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
  /** Ready: what was observed. Deferred: observed cause + the deferral wording. Red: observed cause + fault wording. */
  detail: string
  observed: string
  state: DeferredSubsystemState
  timestamp: number
}

const DEFERRED_SUBSYSTEMS: Record<string, DeferredSubsystemDefinition> = {
  aigw: {
    // Byte-equal to the `aigw` entry in doctor.py's DEFERRED_ENABLE_HINTS — a
    // test pins it, because this is a claim about app behaviour and it must say
    // the same thing wherever the user reads it.
    enableHint: 'Plobi 需要时自己起这一条 —— 没有要人手跑的东西',
    id: 'aigw',
    name: { en: 'Local Quota Hub', zh: '本地额度网关' },
    port: 8000
  },
  chatlog: {
    enableHint:
      '把 sidecar 起来监听 :5030（这台上的二进制在 `tools/chatlog/bin/chatlog.exe`；Go 与 `CHATLOG_DATA_KEY` 齐不齐不是这条探针的读数，所以这里不替它担保）',
    id: 'chatlog',
    name: { en: 'chatlog WeChat collector', zh: '微信采集 chatlog' },
    port: 5030
  }
}

/**
 * Row ids for per-profile Plobi backends, spawned by `spawnPoolBackend()` in
 * main.ts. They share this ledger (and therefore `red`) with the sidecars, but
 * they are NOT part of `DEFERRED_SUBSYSTEMS`: which profiles get attempted is
 * only known at runtime, and they belong to no deferral tier.
 */
const PROFILE_BACKEND_ID_PREFIX = 'backend:'

/** Same shape as the quota hub hint: the app owns the lifecycle, nothing to run. */
const PROFILE_BACKEND_ENABLE_HINT =
  'Plobi brings this profile backend up itself and backs off between attempts — there is nothing to run by hand.'

/**
 * The deferred wording, kept byte-identical to `DEFERRED_SUFFIX` in
 * `scripts/plobi/doctor.py` — one 口径, two renderings (tray + boot payload).
 * It used to cite a ruling number that was revoked on 2026-09-30 — a revoked
 * number cannot be the reason a row reports gray (裁定 162).
 */
const DEFERRED_SUFFIX = 'deferred — 不是坏了：这一档不归「该跑而没跑」那一型，真跑起来自己就变绿。怎么回到绿：'

/**
 * The fault wording for `red`. It deliberately carries NONE of the deferral
 * sentence — that wording is precisely what made a real failure read as an
 * intentional postponement, and `deferred-sidecars.test.ts` pins only the
 * `deferred` branch to doctor.py byte-for-byte.
 */
const RED_SUFFIX = '这一条这台试过，没起来：是坏了，不是后置'

function definitionFor(id: string): DeferredSubsystemDefinition | null {
  return (
    DEFERRED_SUBSYSTEMS[id] ?? profileBackendDefinition(id) ?? keyReconcileDefinition(id) ?? restartRowDefinition(id)
  )
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
 * The gateway-key reconciliation row (`WP-AIGW-KEY-LITERALS`, 裁定 94 ③: a red has
 * to land on a surface the user actually has, not only in `logs/*.log`). Like a
 * profile backend row it is NOT part of `DEFERRED_SUBSYSTEMS` — that table is the
 * deferral tier, is mirrored by `doctor.py`'s `DEFERRED_ENABLE_HINTS`, and
 * this row is neither a sidecar nor a postponement. It has no port either, so it
 * is created by its first observation rather than seeded as `probing`: the check
 * only exists at the moment the shell is about to send a key, and a row that said
 * "still probing" forever would be its own kind of silence.
 */
export const KEY_RECONCILE_ROW_ID = 'aigw-key'

/** Same shape as the quota hub hint: the app owns the lifecycle, nothing to run. */
const KEY_RECONCILE_ENABLE_HINT =
  'make the two sides agree — point the gateway config.yaml `server.api_key` at the same `${VAR:-…}` reference the shell exports, or set the value the gateway declares to the key the shell already uses'

function keyReconcileDefinition(id: string): DeferredSubsystemDefinition | null {
  if (id !== KEY_RECONCILE_ROW_ID) {
    return null
  }

  return {
    enableHint: KEY_RECONCILE_ENABLE_HINT,
    id,
    name: { en: 'Gateway key check', zh: '网关 key 对账' },
    port: 0
  }
}

/**
 * The restart row (`WP-TRAY-RESTART`). The tray button must report its outcome on a
 * surface the user already has — the boot ledger plus the tray menu are those two
 * renderings — instead of only in `logs/desktop.log` (裁定 94 第二款: 只在日志里可见
 * 不算可见). Like the key row it is neither a sidecar with a port nor a
 * deferral, so it is created by its first observation.
 */
export const TRAY_RESTART_ROW_ID = 'tray-restart'

const TRAY_RESTART_ENABLE_HINT =
  'no action needed — the restart re-launches the same tree; if it says it aborted, open Plobi again by hand'

function restartRowDefinition(id: string): DeferredSubsystemDefinition | null {
  if (id !== TRAY_RESTART_ROW_ID) {
    return null
  }

  return {
    enableHint: TRAY_RESTART_ENABLE_HINT,
    id,
    name: { en: 'App restart', zh: '应用重启' },
    port: 0
  }
}

/** Ids that may materialise from their first observation rather than at boot. */
const LAZY_ROW_IDS = new Set<string>([KEY_RECONCILE_ROW_ID, TRAY_RESTART_ROW_ID])

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

  // The deferred branch renders byte-identically to doctor.py's `deferred_row()`
  // — the suffix already ends in "怎么回到绿：", so the hint is appended, not
  // re-labelled. The red branch keeps its own sentence, which doctor does not
  // share (doctor's red reason names the registry switch; this side cannot read
  // that table and must not pretend to).
  const tail =
    row.state === 'red' ? `${RED_SUFFIX}。怎么回到绿：${row.enableHint}` : `${DEFERRED_SUFFIX}${row.enableHint}`

  if (!row.observed) {
    return tail
  }

  return `${row.observed} — ${tail}`
}

/**
 * Create the ledger. `ids` defaults to the two deferred subsystems; a row always
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
     * set of profiles this machine attempts is runtime data — as is the moment
     * the shell first tries to send a key, which creates `aigw-key`. Any other
     * unknown id is still refused.
     */
    record(
      id: string,
      { observed, state }: { observed: string; state: DeferredSubsystemState }
    ): DeferredSubsystemRow | null {
      const definition = definitionFor(id)
      const lazy = id.startsWith(PROFILE_BACKEND_ID_PREFIX) || LAZY_ROW_IDS.has(id)

      if (!definition || (!rows.has(id) && !lazy)) {
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

/**
 * How long a *deterministic* spawn failure stays blocked. Long enough that a
 * 15 s renderer reconnect costs an IPC and a thrown reason instead of a whole
 * `plobi` subprocess boot; short enough that creating the missing profile (or
 * deleting the roster row) heals the row by itself on the next request.
 */
const RESPAWN_PERMANENT_HOLD_MS = 600_000

/**
 * Three shapes of spawn failure are not transient — retrying them proves nothing:
 *   1. the CLI's own "Profile 'X' does not exist. Create it with: …" (exit 1);
 *   2. argparse eating the profile name as a subcommand, "argument command:
 *      invalid choice: 'X'" (exit 2) — what happens when the value is not a
 *      valid profile id at all: the CLI's pre-scan only accepts
 *      `[a-z0-9一-鿿＀-ｴ_-]{1,64}` (`plobi_cli/profiles.py:validate_profile_name`),
 *      and a roster key outside it — e.g. `大学物理（甲）Ⅱ`, whose `Ⅱ` is U+2171 —
 *      never reaches `-p`, so argparse reads the name as a subcommand.
 *      **Building a directory would not fix this shape** —
 *      the name itself is unusable, which is why the roster-side answer had to be
 *      "data rows carry no profile" and not "create the missing profile";
 *   3. "Profile name 'X' is reserved — it collides with either the Plobi
 *      installation itself or a common system binary" — the name passes the regex
 *      but is permanently banned. `Plobi`'s own roster row (`role: l2_project`,
 *      so *not* a data row) is exactly this shape: that row can never be opened,
 *      however many times we try (产品线 13:2x 实测 `plobi -p plobi --help` ⇒ exit 0,
 *      the failure only appears when the backend actually boots).
 *
 * The 2026-10-13 incident this exists for: five `role: l2_course` rows were put
 * in the roster on 2026-10-12 20:1x. Course rows are *data* (裁定 recorded in
 * `plobi/routing/models.py`: no route, not askable), so they have no profile
 * directory — yet the console handed their name out as a `profile`, the renderer
 * reconnect loop asked for a backend for them, and desktop.log filled itself with
 * 2,214 / 2,767 / 280 / 96 identical spawn/exit cycles for profiles that can
 * never come up. (The counts are readings of an append-only file: my own 08:5x
 * pass saw 1,316 for 电工电子学, 产品线's 09:1x re-measure saw 2,767 — both true.)
 */
type SpawnFault = 'missing-profile' | 'unusable-name' | 'reserved-name' | 'transient'

function spawnFaultOf(message: string): SpawnFault {
  if (/is reserved [—-] it collides with/i.test(message)) {
    return 'reserved-name'
  }
  if (/argument command: invalid choice/i.test(message)) {
    return 'unusable-name'
  }
  if (/does not exist\. Create it with:/i.test(message)) {
    return 'missing-profile'
  }
  return 'transient'
}

/** Any of the three permanent shapes: another attempt cannot change the outcome. */
function isMissingProfileFailure(message: string): boolean {
  return spawnFaultOf(message) !== 'transient'
}

/** The honest wording for a permanent row: what is wrong, and the way out — per shape. */
function missingProfileDetail(profile: string, message: string): string {
  const fault = spawnFaultOf(message)
  const tail = `The backend will not be re-spawned on a timer until that changes (CLI said: ${message})`

  if (fault === 'reserved-name') {
    return (
      `This roster row can never be opened: "${profile}" is a reserved profile name — it collides ` +
      'with the Plobi installation itself or a system binary, so no amount of retrying (or creating ' +
      'a directory by hand) will work. Rename the profile this entry points at, or stop treating the ' +
      `entry as a runnable agent. ${tail}`
    )
  }

  if (fault === 'unusable-name') {
    return (
      `This roster row's name never even reaches the profile switch: "${profile}" is not a valid ` +
      'profile id, so argparse read it as a subcommand. Building a profile directory would not help ' +
      '— the name is unusable. That matches what the entry is: a 课程 row carries data, not a ' +
      `runtime agent, so it should not be opened as one. ${tail}`
    )
  }

  return (
    `This roster row has no profile to run as, so nothing was ever listening — "${profile}" ` +
    'is either a data-only entry (a 课程 row is data, not an agent: no model route, not askable) ' +
    `or the profile was never created. Either stop opening it, or run "plobi profile create ${profile}". ${tail}`
  )
}

interface RespawnGate {
  attempt: number
  message: string
  nextAttemptAt: number
  /** Deterministic failure: no timer is armed, and the block reason is said once. */
  permanent: boolean
  reported: boolean
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
     * `autoRetry` says whether the caller should arm a timer at all — a
     * deterministic failure never gets a timer, because a timer would just
     * re-run the same exit within the same second, forever.
     */
    arm(profile: string, message: string): {
      attempt: number
      autoRetry: boolean
      delayMs: number
      permanent: boolean
    } {
      const attempt = (gates.get(profile)?.attempt ?? 0) + 1
      const backoff = Math.min(maxDelayMs, baseDelayMs * 2 ** (attempt - 1))
      const permanent = isMissingProfileFailure(message)
      const delayMs = permanent ? Math.max(backoff, RESPAWN_PERMANENT_HOLD_MS) : backoff

      gates.set(profile, { attempt, message, nextAttemptAt: now() + delayMs, permanent, reported: false })

      return { attempt, autoRetry: permanent ? false : attempt <= maxAutoRetries, delayMs, permanent }
    },

    /**
     * Why this profile may not be spawned right now, or `null` when a spawn is
     * allowed. The message carries the previous failure verbatim so a caller who
     * is blocked still learns the cause instead of getting a bare refusal.
     *
     * A permanent gate says the *cause* once per window: the ledger row already
     * holds it, and the renderer reconnects on a fixed interval, so repeating it
     * is exactly how one roster row wrote 2,214 identical lines into the log.
     */
    blockReason(profile: string): string | null {
      const gate = gates.get(profile)

      if (!gate || now() >= gate.nextAttemptAt) {
        return null
      }

      const waitSeconds = Math.ceil((gate.nextAttemptAt - now()) / 1000)

      if (gate.permanent && gate.reported) {
        return (
          `Plobi backend for profile "${profile}" is not spawnable (next try in ${waitSeconds}s; ` +
          'the cause is already on the status row)'
        )
      }

      gate.reported = true

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

/**
 * 后置服务（chatlog 这类我们 spawn 的子进程）的**有界重拉**计划。
 *
 * 为什么要有这一支：`ensureChatlogServer()` 以前只在后端 ready 那一下被调用一次，
 * 于是子进程半路死了（微信退登录、被外部杀掉、自己崩）就再也没人拉——那正是"该自己起来的东西
 * 不起"的产品缺陷（裁 176：无感知、自动拉起、出错才说话）。
 * 为什么形状长成这样而不是一个固定轮询：这文件上面记着两次事故（3,900 与 1,173 次相同 spawn 循环、
 * 2,214 行相同台账），所以重试必须**由退出事件驱动 + 指数退避 + 次数封顶**，
 * 且"我们自己关掉的"绝不能排重试（否则每次正常重启都会多拉一发）。
 */
function createSidecarRespawnPlan({
  baseDelayMs = RESPAWN_BASE_DELAY_MS,
  maxAutoRetries = RESPAWN_MAX_AUTO_RETRIES,
  maxDelayMs = RESPAWN_MAX_DELAY_MS
}: {
  baseDelayMs?: number
  maxAutoRetries?: number
  maxDelayMs?: number
} = {}) {
  let attempt = 0

  return {
    /** 起来了／是我们自己关的 ⇒ 计数归零，下一次意外退出从最短间隔重新开始。 */
    reset(): void {
      attempt = 0
    },
    attempts(): number {
      return attempt
    },
    /** 还要再试就返回这次的间隔；到顶了就返回 autoRetry=false，调用方只报一句人话，不再排定时器。 */
    fail(): { attempt: number; autoRetry: boolean; delayMs: number } {
      attempt += 1
      return { attempt, autoRetry: attempt <= maxAutoRetries, delayMs: Math.min(maxDelayMs, baseDelayMs * 2 ** (attempt - 1)) }
    }
  }
}

export {
  createBackendRespawnGuard,
  createSidecarRespawnPlan,
  createDeferredSubsystemLedger,
  DEFERRED_SUBSYSTEMS,
  DEFERRED_SUFFIX,
  PROFILE_BACKEND_ENABLE_HINT,
  PROFILE_BACKEND_ID_PREFIX,
  RED_SUFFIX,
  RESPAWN_BASE_DELAY_MS,
  RESPAWN_MAX_AUTO_RETRIES,
  RESPAWN_MAX_DELAY_MS,
  RESPAWN_PERMANENT_HOLD_MS,
  deferredDetail,
  definitionFor,
  isMissingProfileFailure,
  missingProfileDetail,
  profileBackendRowId,
  spawnFaultOf
}
