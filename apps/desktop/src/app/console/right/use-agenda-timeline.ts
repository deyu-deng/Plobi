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
import { useEffect, useState } from 'react'

import { getAgenda } from '@/plobi'
import {
  $agendaEvents,
  setAgendaError,
  setAgendaEvents,
  setAgendaLoading
} from '@/store/agenda'
import { $busy } from '@/store/session'
import type { AgendaAnchor, AgendaAvoidWindow, AgendaDay, AgendaEvent, AgendaPlanItem } from '@/types/plobi'

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

// ---------------------------------------------------------------------------
// WP-AGENDA-RAIL-APPLE: 一屏只画**选中的那一天**
//
// 上面那套 poll 的口径一个字不动（今天+明天、写进共享的 `@/store/agenda`，
// 全屏看板 `app/agenda` 也读它）。这一节只加两件事：
//   - 选中别的日子时，按那一天单独取一次（`/api/agenda?from&to` + `/api/agenda/day`），
//     结果缓存在模块级 Map 里，同一周来回点不再重复打后端；
//   - 抬头那一周的号码下面要点小圆点，所以整周也要有一份"哪天有几件事"的计数。
// 今天永远走共享 store 的现成数据（seed），不另取一份 —— 两处真源会各说各话。
// ---------------------------------------------------------------------------

export interface RailDay {
  anchors: AgendaAnchor[]
  avoidWindows: AgendaAvoidWindow[]
  events: AgendaEvent[]
  plans: AgendaPlanItem[]
}

export const EMPTY_RAIL_DAY: RailDay = { anchors: [], avoidWindows: [], events: [], plans: [] }

/** 后端认的是带 `T` 的完整本地 ISO（与 `tools/agenda_tools.py` 的 `_bound()` 同一口径）。 */
function dayWindow(dateKey: string): [string, string] {
  return [`${dateKey}T00:00:00`, `${dateKey}T23:59:59`]
}

export function shiftDateKey(dateKey: string, deltaDays: number): string {
  const date = new Date(`${dateKey}T00:00:00`)

  date.setDate(date.getDate() + deltaDays)

  return localDateKey(date)
}

/** 抬头那一周从周日开始（Apple 日历中文区默认也是日头）。 */
export function weekStartOf(dateKey: string): string {
  return shiftDateKey(dateKey, -new Date(`${dateKey}T00:00:00`).getDay())
}

export function weekKeysOf(dateKey: string): string[] {
  const start = weekStartOf(dateKey)

  return Array.from({ length: 7 }, (_, offset) => shiftDateKey(start, offset))
}

const dayCache = new Map<string, RailDay>()
const dayInFlight = new Map<string, Promise<RailDay>>()

/**
 * 取数走的是桌面那条 IPC 桥。桥不在（渲染层单独挂载、单测、后端还没起来的空窗）
 * 就**什么都不取**：调用方拿到空集，轴照旧画刻度，绝不拿别处的数据冒充那一天。
 */
function agendaBridgeReady(): boolean {
  return Boolean((window as unknown as { plobiDesktop?: { api?: unknown } }).plobiDesktop?.api)
}

function loadDay(dateKey: string): Promise<RailDay> {
  const cached = dayCache.get(dateKey)

  if (cached) {
    return Promise.resolve(cached)
  }

  if (!agendaBridgeReady()) {
    return Promise.resolve(EMPTY_RAIL_DAY)
  }

  const pending = dayInFlight.get(dateKey)

  if (pending) {
    return pending
  }

  const [from, to] = dayWindow(dateKey)
  const request = Promise.all([getAgenda(from, to), fetchDayPayload(dateKey)])
    .then(([events, day]) => {
      const loaded: RailDay = {
        anchors: day?.anchors ?? [],
        avoidWindows: day?.avoid_windows ?? [],
        events,
        plans: day?.plan_items ?? []
      }

      dayCache.set(dateKey, loaded)

      return loaded
    })
    .catch(() => EMPTY_RAIL_DAY)
    .finally(() => {
      dayInFlight.delete(dateKey)
    })

  dayInFlight.set(dateKey, request)

  return request
}

/**
 * 选中那一天的四样数据。`coveredKeys` 是 seed（共享 store 那份今天+明天的窗）
 * 真的覆盖到的日子——落在里面的直接用 seed，不另取一份，两处真源会各说各话；
 * 别的日子现取。取不到就是空轴，不拿今天的数据冒充别的日子。
 */
export function useAgendaRailDay(dateKey: string, seed: RailDay, coveredKeys: string[]): RailDay {
  const covered = coveredKeys.includes(dateKey)
  const [loaded, setLoaded] = useState<RailDay>(() => dayCache.get(dateKey) ?? EMPTY_RAIL_DAY)

  useEffect(() => {
    if (covered) {
      return
    }

    const cached = dayCache.get(dateKey)

    if (cached) {
      setLoaded(cached)

      return
    }

    let cancelled = false

    void loadDay(dateKey).then(day => {
      if (!cancelled) {
        setLoaded(day)
      }
    })

    return () => {
      cancelled = true
    }
  }, [dateKey, covered])

  return covered ? seed : loaded
}

/** 抬头那一周每天有几件事（小圆点的个数）。 */
export function useAgendaRailWeekCounts(dateKey: string, seedEvents: AgendaEvent[]): Record<string, number> {
  const week = weekKeysOf(dateKey)
  const signature = week.join('|')
  const [rows, setRows] = useState<AgendaEvent[]>([])

  useEffect(() => {
    if (!agendaBridgeReady()) {
      setRows([])

      return
    }

    let cancelled = false
    const [from, to] = [`${week[0]}T00:00:00`, `${week[6]}T23:59:59`]

    void getAgenda(from, to)
      .then(events => {
        if (!cancelled) {
          setRows(events)
        }
      })
      .catch(() => {
        if (!cancelled) {
          setRows([])
        }
      })

    return () => {
      cancelled = true
    }
    // signature 就是这一周七天；换周才重取。
  }, [signature])

  const counts: Record<string, number> = {}

  // 共享 store 那份（今天+明天的窗）先算进来：周取数回来之前圆点不闪空。
  for (const event of [...rows, ...seedEvents]) {
    const key = (event.start_at ?? '').slice(0, 10)

    counts[key] = (counts[key] ?? 0) + 1
  }

  return counts
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
