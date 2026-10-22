import type { AgendaConflict, AgendaDay } from '@/types/plobi'

/**
 * WP-AGENDA-VIEW slice 2/3 — the seven day snapshots behind the week row.
 *
 * Why a fetch exists here at all: 判据 3 says conflicts must be *visible* in the week view, and
 * `planning.py` only reports them per day (`/api/agenda/day?date=…` → `conflicts`). The phone side
 * reached the same conclusion and the user signed it as **D1** (循环 7 次 `fetchDay`,
 * `Docs/tasks/app-calendar-week-task.md`), with **C1 渐进** (render the bucketing first, fill the
 * conflict layer as each day lands) and **C2 单列降级** (one day failing must not blank the week).
 * This module follows that same shape so the two surfaces stay one design, not two (裁定 139④(b)).
 *
 * What it never does: derive an overlap from event rows. A day whose request failed is *absent*
 * from the map — and the column then says 「冲突未取」 instead of claiming a clean day.
 *
 * 日程＝计划（用户 10-22）: the plan layer is retired, so this transport no longer carries
 * `plan_items` across the week row. It asks only for the conflict layer.
 */

const DAY_ENDPOINT = '/api/agenda/day'
/** Same wall-clock bound the console rail uses: the desktop bridge takes no AbortSignal. */
const DAY_TIMEOUT_MS = 3000

export type DayFetchResult =
  | {
      conflicts: AgendaConflict[] | undefined
      dayKey: string
      ok: true
    }
  | { dayKey: string; error: unknown; ok: false }

export type DayFetcher = (dayKey: string) => Promise<AgendaDay | null>

export interface WeekConflictState {
  /** Only days the backend actually answered. Missing key = unknown, **not** zero conflicts. */
  conflictsByDay: Record<string, AgendaConflict[]>
  /** How many of the visible days resolved — for the "N/7 已取" style honesty if the UI wants it. */
  loadedCount: number
}

function conflictsOf(payload: AgendaDay | null): AgendaConflict[] | undefined {
  if (!payload) {
    return undefined
  }

  // An older backend shape omits `conflicts`; that is "unknown", not "clean".
  return Array.isArray(payload.conflicts) ? payload.conflicts : undefined
}

/** Fold per-day outcomes into the map the strip renders. Pure. */
export function reduceWeekConflicts(results: DayFetchResult[]): WeekConflictState {
  const conflictsByDay: Record<string, AgendaConflict[]> = {}
  let loadedCount = 0

  for (const result of results) {
    if (!result.ok || result.conflicts === undefined) {
      continue
    }

    conflictsByDay[result.dayKey] = result.conflicts
    loadedCount += 1
  }

  return { conflictsByDay, loadedCount }
}

/**
 * Ask for each day, keep going when one fails (C2).
 *
 * `fetchDay` is injected so the caller owns the transport and the test owns the failure modes;
 * nothing here touches `window`.
 */
export async function fetchWeekConflicts(
  dayKeys: string[],
  fetchDay: DayFetcher
): Promise<WeekConflictState> {
  const settled = await Promise.all(
    dayKeys.map(async (dayKey): Promise<DayFetchResult> => {
      try {
        const payload = await fetchDay(dayKey)
        const conflicts = payload ? conflictsOf(payload) : undefined

        return !payload || conflicts === undefined
          ? { dayKey, error: new Error('no conflict layer in payload'), ok: false }
          : { conflicts, dayKey, ok: true }
      } catch (error) {
        return { dayKey, error, ok: false }
      }
    })
  )

  return reduceWeekConflicts(settled)
}

/** The production fetcher: one GET per day, wall-clock bounded, `null` when it does not answer. */
export function createDayFetcher(): DayFetcher {
  return async (dayKey: string) => {
    // The desktop api is a thin IPC wrapper and takes no AbortSignal, so the wall clock bounds
    // it — same discipline as `console/right/use-agenda-timeline.ts`, one late day must not
    // hold the whole week's conflict layer open.
    let timer = 0

    const deadline = new Promise<null>(resolve => {
      timer = window.setTimeout(() => resolve(null), DAY_TIMEOUT_MS)
    })

    try {
      return await Promise.race([
        window.plobiDesktop
          .api<AgendaDay>({
            method: 'GET',
            path: `${DAY_ENDPOINT}?date=${encodeURIComponent(dayKey)}`
          })
          .catch(() => null),
        deadline
      ])
    } finally {
      window.clearTimeout(timer)
    }
  }
}
