import type { DesktopDeferredSubsystem } from '@/global'
import { currentI18nLocale, translateNow } from '@/i18n'
import { $notifications, dismissNotification, notify } from '@/store/notifications'

// ─────────────────────────────────────────────────────────────────────────────
// R-051 — rendering #2 of the deferred-subsystem ledger, split by state.
//
// The rows are owned by the MAIN process (electron/deferred-sidecars.ts) and are
// broadcast on the boot-progress channel; the tray menu renders the very same
// array. This module does not derive status, keep a parallel store, or decide
// anything about health — it only turns the rows the main process already
// observed into the two DIFFERENT things they mean:
//
//   * `red`  — attempted here and never came up healthy. THIS and only this
//              raises the dismissible alarm card. A real fault must be visible.
//   * `deferred` — nothing on this machine could even be started (no binary, no
//              gateway config), postponed by R-047. NO card. It folds into the
//              existing statusbar gateway dot, whose menu still lists every row
//              with its reason and its enable steps.
//
// Branching is on `row.state`, never on the detail string — the deferred wording
// is byte-pinned to scripts/plobi/doctor.py by electron/deferred-sidecars.test.ts,
// so a string test would break the guard and hide the semantics again.
//
// Neither state may become boot.error: a sidecar must never hold the boot screen
// open or cost the user the conversation (see src/app/gateway/hooks/use-gateway-boot.ts).
// The moment a row flips to `ready` its notice goes away by itself.
// ─────────────────────────────────────────────────────────────────────────────

/** Stable notice-id prefix, so repeat broadcasts update in place instead of stacking. */
export const DEFERRED_NOTICE_PREFIX = 'deferred-subsystem:'

export function deferredNoticeId(subsystemId: string): string {
  return `${DEFERRED_NOTICE_PREFIX}${subsystemId}`
}

/**
 * Localized display name of a row. The main process ships both languages because
 * the Electron side has no i18n catalog of its own; the renderer picks.
 */
export function deferredRowLabel(row: DesktopDeferredSubsystem, locale: string = currentI18nLocale()): string {
  return locale.startsWith('zh') ? row.name.zh : row.name.en
}

/**
 * The single place the two states are told apart. Every consumer — the alarm-card
 * planner below and the statusbar fold — goes through these lists, so nobody can
 * quietly re-merge `red` into `deferred` again.
 */
export interface DeferredLedgerView {
  /** Real faults: the only rows entitled to an alarm card. */
  red: DesktopDeferredSubsystem[]
  /** Postponed by R-047: silent in the window, listed in the statusbar menu. */
  deferred: DesktopDeferredSubsystem[]
  /** Everything the statusbar dot should account for: red plus deferred. */
  noted: DesktopDeferredSubsystem[]
}

export function readDeferredLedgerView(rows?: DesktopDeferredSubsystem[]): DeferredLedgerView {
  const all = rows ?? []

  const red = all.filter(row => row.state === 'red')
  const deferred = all.filter(row => row.state === 'deferred')

  return { deferred, noted: [...deferred, ...red], red }
}

/**
 * The one line the statusbar gateway dot adds when the ledger has something to
 * say. Returns `null` when nothing is postponed or broken, so the dot goes back to
 * talking about the gateway alone — that is how "the service came up" makes the
 * gray dot disappear without a second indicator.
 */
export function deferredStatusbarCount(rows?: DesktopDeferredSubsystem[]): number {
  return readDeferredLedgerView(rows).noted.length
}

export interface DeferredNoticePlan {
  /** Toast ids currently on screen that no longer correspond to a red row. */
  dismiss: string[]
  /** Red rows with no toast on screen yet. */
  show: DesktopDeferredSubsystem[]
}

/**
 * Pure diff between the ledger and what is already on screen. Kept separate from
 * the DOM/notify side effects so it can be tested as a plain function.
 *
 * Idempotent on both axes: an unchanged `red` row produces no new toast, and a
 * `ready` row produces none either — so a chatty broadcast cannot flood the
 * notification stack.
 *
 * `deferred` and `probing` rows are never announced: a postponement is a priority
 * decision, and drawing it as a fault is the false state R-048 removed.
 *
 * Only ids carrying `DEFERRED_NOTICE_PREFIX` are ever eligible for dismissal:
 * this module must never withdraw a notice it does not own.
 */
export function planDeferredSubsystemNotices(
  rows: DesktopDeferredSubsystem[],
  visibleNoticeIds: string[]
): DeferredNoticePlan {
  const alarming = readDeferredLedgerView(rows).red
  const wanted = new Set(alarming.map(row => deferredNoticeId(row.id)))
  const shown = new Set(visibleNoticeIds.filter(id => id.startsWith(DEFERRED_NOTICE_PREFIX)))

  return {
    dismiss: [...shown].filter(id => !wanted.has(id)),
    show: alarming.filter(row => !shown.has(deferredNoticeId(row.id)))
  }
}

/** Which deferred notices the renderer is currently showing. */
export function visibleDeferredNoticeIds(): string[] {
  return $notifications
    .get()
    .map(notification => notification.id)
    .filter(id => id.startsWith(DEFERRED_NOTICE_PREFIX))
}

/**
 * Render the ledger into the window. Safe to call on every boot-progress payload,
 * including after cold boot has completed — that is the whole point, since the
 * sidecars are probed after the backend is already ready.
 *
 * Only `red` rows raise a notice. `deferred` rows are announced by the statusbar
 * gateway dot instead (see `src/app/shell/hooks/use-statusbar-items.tsx`), so a
 * postponed service costs the user nothing but a gray dot they can open.
 */
export function reportDeferredSubsystems(rows?: DesktopDeferredSubsystem[]): void {
  if (!rows || rows.length === 0) {
    return
  }

  const plan = planDeferredSubsystemNotices(rows, visibleDeferredNoticeIds())

  for (const id of plan.dismiss) {
    dismissNotification(id)
  }

  for (const row of plan.show) {
    notify({
      detail: translateNow('boot.sidecars.redHint'),
      id: deferredNoticeId(row.id),
      // `warning` is the persistent, top-centre kind (durationMs 0) — a real
      // sidecar fault must stay readable until the user acknowledges it, but
      // unlike `error` it does not gate or fail the boot.
      kind: 'warning',
      message: row.detail,
      title: `${translateNow('boot.sidecars.redTitle')} · ${deferredRowLabel(row)}${
        row.port > 0 ? ` (:${row.port})` : ''
      }`
    })
  }
}
