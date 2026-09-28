import type { DesktopDeferredSubsystem } from '@/global'
import { currentI18nLocale, translateNow } from '@/i18n'
import { $notifications, dismissNotification, notify } from '@/store/notifications'

// ─────────────────────────────────────────────────────────────────────────────
// R-051 — rendering #2 of the deferred-subsystem ledger.
//
// The rows are owned by the MAIN process (electron/deferred-sidecars.ts) and are
// broadcast on the boot-progress channel; the tray menu renders the very same
// array. This module does not derive status, keep a parallel store, or decide
// anything about health — it only turns rows that the main process already
// observed into `deferred` into a non-blocking notice in the window.
//
// The contract it enforces in the UI:
//   * a postponed subsystem that failed shows "this did not come up, here is
//     why" — it never silently disappears and is never shown as success;
//   * the notice is a dismissible warning toast, NOT a modal and NOT boot.error,
//     so the main conversation stays usable while it is on screen;
//   * the moment the row flips to `ready` the notice goes away by itself.
//
// Mirrors the `deferred` fourth status of scripts/plobi/doctor.py (commit
// 4b437be): probe anyway, downgrade the failure rather than the check, light up
// as soon as it is really alive.
// ─────────────────────────────────────────────────────────────────────────────

/** Stable toast-id prefix, so repeat broadcasts update in place instead of stacking. */
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

export interface DeferredNoticePlan {
  /** Toast ids currently on screen that no longer correspond to a deferred row. */
  dismiss: string[]
  /** Deferred rows with no toast on screen yet. */
  show: DesktopDeferredSubsystem[]
}

/**
 * Pure diff between the ledger and what is already on screen. Kept separate from
 * the DOM/notify side effects so it can be tested as a plain function.
 *
 * Idempotent on both axes: an unchanged `deferred` row produces no new toast, and
 * a `ready` row produces none either — so a chatty broadcast cannot flood the
 * notification stack.
 *
 * Only ids carrying `DEFERRED_NOTICE_PREFIX` are ever eligible for dismissal:
 * this module must never withdraw a notice it does not own.
 */
export function planDeferredSubsystemNotices(
  rows: DesktopDeferredSubsystem[],
  visibleNoticeIds: string[]
): DeferredNoticePlan {
  const deferred = rows.filter(row => row.state === 'deferred')
  const wanted = new Set(deferred.map(row => deferredNoticeId(row.id)))
  const shown = new Set(visibleNoticeIds.filter(id => id.startsWith(DEFERRED_NOTICE_PREFIX)))

  return {
    dismiss: [...shown].filter(id => !wanted.has(id)),
    show: deferred.filter(row => !shown.has(deferredNoticeId(row.id)))
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
      detail: translateNow('boot.sidecars.deferredHint'),
      id: deferredNoticeId(row.id),
      // `warning` is the persistent, top-centre kind (durationMs 0) — a deferred
      // subsystem must stay readable until the user acknowledges it, but unlike
      // `error` it does not gate or fail the boot.
      kind: 'warning',
      message: row.detail,
      title: `${deferredRowLabel(row)} (:${row.port})`
    })
  }
}
