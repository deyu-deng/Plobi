/**
 * Right-rail agenda timeline (U3, spec §3.3).
 *
 * The data is the SAME store the full-screen board (`app/agenda`) renders
 * (`@/store/agenda` → `getAgenda` in `@/plobi`) — there is exactly one
 * `events` source of truth, per spec §3.3 "完全同一份". This hook just makes
 * sure that store is populated and kept fresh (≤10s, ADR-0008) when the
 * console is the only mounted consumer. The board's own poll is independent
 * and writes the same atoms, so whichever view mounts first owns the poll.
 *
 * WP-DAY-FACE (裁定 31.4): also seeds routine blocks (anchors / plan_items)
 * from `GET /api/agenda/day?date=…` so the same axis paints meals and
 * planned work alongside events. The endpoint is optional — when it 404s or
 * times out, the rail quietly falls back to events-only and `agendaStore
 * .rhythmOff` flips true so the UI can show "作息暂未同步".
 */

import { atom } from 'nanostores'
import { useEffect } from 'react'

import {
  $agendaEvents,
  setAgendaError,
  setAgendaEvents,
  setAgendaLoading
} from '@/store/agenda'
import { $busy } from '@/store/session'
import type { AgendaAnchor, AgendaAvoidWindow, AgendaDay, AgendaPlanItem } from '@/types/plobi'

import { fetchAllAgendaEvents } from '../api'

// ADR-0008 SLA: changes visible ≤10s.  Poll at 8s to stay inside the SLA
// with margin for network latency.  Future: upgrade to push (SSE/WebSocket)
// when the backend supports it.
const POLL_INTERVAL_MS = 8000
const DAY_ENDPOINT = '/api/agenda/day'
const DAY_TIMEOUT_MS = 3_000

let inFlight: Promise<void> | null = null
let queued = false

export const $agendaAnchors = atom<AgendaAnchor[]>([])
export const $agendaPlans = atom<AgendaPlanItem[]>([])
/**
 * Confirmed avoid-windows (R-39 / WP-AXIS-TODAY). Backend 2 may surface these
 * on `GET /api/agenda/day`; the rail paints them as faint untitled bands and
 * the renderer simply skips the atom when the field is absent (裁定 39 says
 * missing / empty = no bands, older fixtures stay green).
 */
export const $agendaAvoidWindows = atom<AgendaAvoidWindow[]>([])
/**
 * True when the day endpoint 404'd / timed out — the rail falls back to
 * events-only and shows the quiet banner. The board can ignore this atom
 * entirely (it only paints the banner on the rail).
 */
export const $agendaRhythmOff = atom(false)

async function fetchDayPayload(date: string): Promise<AgendaDay | null> {
  // The desktop api is a thin IPC wrapper — it does NOT accept AbortSignal.
  // We bound the wall clock instead and swallow the rejection so the rail
  // never hangs past 3s when the endpoint is unreachable.
  let timedOut = false
  const timeout = window.setTimeout(() => {
    timedOut = true
  }, DAY_TIMEOUT_MS)

  try {
    return await window.plobiDesktop.api<AgendaDay>({
      method: 'GET',
      path: `${DAY_ENDPOINT}?date=${encodeURIComponent(date)}`
    })
  } catch {
    return timedOut ? null : null
  } finally {
    window.clearTimeout(timeout)
  }
}

/**
 * Local calendar day (`YYYY-MM-DD`) for an instant.
 *
 * NOT `toISOString().slice(0, 10)`: that is the **UTC** day, so east of
 * Greenwich every morning before the offset is covered the rail asked the
 * backend for *yesterday*. At UTC+8, local 09-16 07:00 is UTC 09-15 23:00 →
 * `/api/agenda/day?date=2026-09-15` returned yesterday's meals and sleep while
 * the agenda events (fetched without a date) were today's — the axis then
 * looked broken again (裁定 33.5).
 */
export function localDateKey(date: Date): string {
  const pad = (value: number) => String(value).padStart(2, '0')

  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
}

async function fetchRoutine(date: Date): Promise<{
  anchors: AgendaAnchor[]
  avoid: AgendaAvoidWindow[]
  day: null | AgendaDay
  plans: AgendaPlanItem[]
  rhythmOff: boolean
}> {
  const dateKey = localDateKey(date)
  const day = await fetchDayPayload(dateKey)

  if (!day) {
    return { anchors: [], avoid: [], day: null, plans: [], rhythmOff: true }
  }

  return {
    anchors: day.anchors ?? [],
    avoid: day.avoid_windows ?? [],
    day,
    plans: day.plan_items ?? [],
    rhythmOff: false
  }
}

async function refresh(): Promise<void> {
  if (inFlight) {
    queued = true

    return inFlight
  }

  setAgendaLoading(true)

  inFlight = (async () => {
    try {
      const [rows, routine] = await Promise.all([fetchAllAgendaEvents(), fetchRoutine(new Date())])

      setAgendaEvents(rows)
      setAgendaError(null)
      $agendaAnchors.set(routine.anchors)
      $agendaPlans.set(routine.plans)
      $agendaAvoidWindows.set(routine.avoid)
      $agendaRhythmOff.set(routine.rhythmOff)
    } catch (error) {
      setAgendaError(error instanceof Error ? error.message : String(error))
      $agendaRhythmOff.set(true)
    } finally {
      setAgendaLoading(false)
      inFlight = null

      if (queued) {
        queued = false
        void refresh()
      }
    }
  })()

  return inFlight
}

/** Force an immediate refresh — wired to the rail's manual retry action. */
export async function refreshAgendaTimeline(): Promise<void> {
  await refresh()
}

/** L1 right rail should refetch as soon as a turn settles, not wait for the 8s poll. */
export function shouldRefreshAgendaOnBusyChange(previousBusy: boolean, nextBusy: boolean): boolean {
  return previousBusy && !nextBusy
}

/**
 * Poll/seed the agenda store only while the consumer is actually mounted as
 * the L1 right rail (WP-D). `enabled: false` (L2, legacy chat, full-pane
 * views) leaves the store untouched — no seed, no interval — so a non-secretary
 * route never keeps an 8s network loop alive.
 *
 * WP-G2: when `$busy` falls (a `submitText` turn finished), refresh immediately
 * so a secretary_ask pipeline's SQLite writes show up without waiting for poll.
 */
export function useAgendaTimeline(enabled: boolean): void {
  useEffect(() => {
    if (!enabled) {
      return
    }

    // `$agendaLoading` starts true (spinner). That is "not fetched yet", not
    // "someone else's request is in flight" — always seed an empty store.
    if ($agendaEvents.get().length === 0) {
      void refresh()
    }

    const intervalId = window.setInterval(() => {
      void refresh()
    }, POLL_INTERVAL_MS)

    let previousBusy = $busy.get()
    const unsubscribeBusy = $busy.listen(busy => {
      if (shouldRefreshAgendaOnBusyChange(previousBusy, busy)) {
        void refresh()
      }

      previousBusy = busy
    })

    return () => {
      window.clearInterval(intervalId)
      unsubscribeBusy()
    }
  }, [enabled])
}
