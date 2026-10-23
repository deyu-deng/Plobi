/** A confirmed slot the user reserved (e.g. focus block, lunch). Drawn as
 *  a faint, untitled, pointer-events:none band behind the regular blocks.
 *  Same naive local-ISO + axis-clamped cross-midnight rules as events. */
export interface TimelineAvoidBand {
  heightPx: number
  topPx: number
}

/**
 * Day-axis layout for the L1 right-rail agenda timeline (R-009 / R-020 / R-031.1).
 *
 * Pure math, no React and no i18n — `timeline.tsx` renders exactly what this
 * returns, and the vitest suite pins the arithmetic (hour span, block height,
 * open-ended events, side-by-side columns) without mounting anything.
 *
 * One block lane: the real agenda rows (the user's meetings / events). The
 * old planned-work lane retired with the plan layer (10-22) — the nightly
 * planner writes events, so there is nothing separate left to paint.
 *
 * Rules that come straight from the rulings:
 * - The axis is the **whole local day**: 00:00 → 24:00, every hour ticked. An
 *   early or late entry is never clipped and the rail never rescales itself
 *   between days (用户 2026-10-21：「每天的时间轴从 0:00 开始算」).
 * - A block's height is exactly `start_at` → `end_at` — 该多长就多长, no
 *   readability floor (用户 2026-10-21). A missing `end_at` is NOT stretched
 *   into a fake hour: it is a **deadline**, laid out as a zero-height row at its
 *   instant and flagged `openEnded` so the UI draws one line across the day.
 * - Side-by-side overlaps (裁定 31.1 / 36.2): one grid, so
 *   blocks that collide in time split into columns and
 *   each takes `1 / columnCount` of the axis width, where columnCount is the
 *   widest column of that block's own overlap cluster. Only the blocks whose
 *   intervals actually intersect are flagged `overlaps`. Deadline rows do
 *   NOT participate in the overlap test — they span the full width as a line.
 * - A block's vertical position is `topPx` (px from the axis top); the renderer
 *   MUST apply it, otherwise every block stacks at the top (裁定 32.1).
 */

import type { AgendaAvoidWindow, AgendaEvent } from '@/types/plobi'

/** The axis always starts at local midnight. */
export const TIMELINE_START_HOUR = 0
/** ... and ends at the last ticked hour; the span runs to 24:00. */
export const TIMELINE_END_HOUR = 23

/** Pixels per hour on the day axis. */
export const HOUR_HEIGHT = 44
/** Horizontal width fraction reserved for the hour-label gutter. */
export const LABEL_GUTTER_FRACTION = 0.18
/** Outer axis padding (px) so the leftmost column has breathing room. */
export const LANE_INSET_PX = 6

export function dayKeyOf(iso: string): string {
  return (iso ?? '').slice(0, 10)
}

/** Clock part of a naive local ISO timestamp (`2026-09-11T09:30:00` → `09:30`). */
export function clockOf(iso: string): string {
  return iso && iso.length >= 16 ? iso.slice(11, 16) : iso
}

/** Minutes since midnight for a naive local ISO timestamp. */
export function minutesOf(iso: string): number {
  if (!iso || iso.length < 16) {
    return 0
  }

  const hours = Number(iso.slice(11, 13))
  const minutes = Number(iso.slice(14, 16))
  const safeHours = Number.isFinite(hours) ? hours : 0
  const safeMinutes = Number.isFinite(minutes) ? minutes : 0

  return safeHours * 60 + safeMinutes
}

export interface TimelineBlock {
  /** Overlap column index (0-based) — the renderer places the block left→right. */
  column: number
  /** `end_at` clock, or null when the entry is open-ended. */
  endClock: null | string
  /** Horizontal fraction (0–1) of the axis this block occupies. */
  widthFraction: number
  /** True when this block shares its column slice with another at the same time. */
  overlaps: boolean
  /** `end_at` is missing (never guessed) — a deadline: height 0, no overlap
   *  test, and the UI draws it as one line across the day with the title on it. */
  openEnded: boolean
  /** The source row: an agenda event. */
  source: AgendaEvent
  /** Stable id from `source.id` when present. */
  id: null | string
  /** Title text to render in the block. */
  title: string
  /** Agenda event status. */
  status: AgendaEvent['status'] | null
  /** Optional location text — pulled from `AgendaEvent.location` (manual) or
   *  `evidence.location` (extracted) — never both, the renderer picks. */
  location: null | string
  /** Block top (px from the axis top). */
  topPx: number
  /** Block height (px). */
  heightPx: number
  /** Block start (minutes since midnight). */
  startMinutes: number
}

export interface TimelineDay {
  /** Confirmed avoid-windows, full-width untitled bands behind the blocks.
   *  Empty array == no bands today (the renderer draws nothing). */
  avoidBands: TimelineAvoidBand[]
  /** Axis width fraction available for blocks (the rest is the hour-label gutter). */
  axisContentFraction: number
  blocks: TimelineBlock[]
  /** Whole hours to draw as ticks. */
  hours: number[]
  key: string
  /** Axis end (minutes since midnight). */
  spanEndMinutes: number
  /** Axis start (minutes since midnight). */
  spanStartMinutes: number
  totalHeightPx: number
}

/**
 * A minimum day axis: the default 00:00–24:00 ticks and a flat height. Used when
 * today carries no events or avoid windows — the rail still paints an
 * empty "today" axis (裁定 37.2 / 39), so the user can see the now line and the
 * day's structure.
 */
export function emptyDay(dayKey: string): TimelineDay {
  const spanStartMinutes = TIMELINE_START_HOUR * 60
  const spanEndMinutes = (TIMELINE_END_HOUR + 1) * 60

  const hours: number[] = []
  for (let hour = spanStartMinutes / 60; hour * 60 < spanEndMinutes; hour += 1) {
    hours.push(hour)
  }

  return {
    avoidBands: [],
    axisContentFraction: 1 - LABEL_GUTTER_FRACTION,
    blocks: [],
    hours,
    key: dayKey,
    spanEndMinutes,
    spanStartMinutes,
    totalHeightPx: Math.round(pxOf(spanEndMinutes, spanStartMinutes))
  }
}

function pxOf(minutes: number, spanStartMinutes: number): number {
  return ((minutes - spanStartMinutes) / 60) * HOUR_HEIGHT
}

/**
 * Block height for a `start → end` pair on this axis.
 *
 * A same-day row whose end precedes its start (e.g. 23:30 → 07:30 written as
 * one date) crosses midnight: it is painted down to the axis bottom rather
 * than producing a negative height (裁定 32.1). Open-ended rows keep the
 * honest minimum — never a fabricated hour.
 */
function blockHeightPx(startMinutes: number, endMinutes: number, spanEndMinutes: number): number {
  // 跨午夜的行（如 23:30 → 07:30 写成同一天）画到轴尾，不给出负高度（裁定 32.1）。
  const clampedEnd = endMinutes < startMinutes ? spanEndMinutes : endMinutes

  // 该多长就多长：没有可读性下限，短会就是一条窄边。
  return Math.round(((clampedEnd - startMinutes) / 60) * HOUR_HEIGHT)
}

/**
 * End used by the one-grid layout. An open-ended row is zero-length (never
 * swallows a column — 裁定 32.1), and a same-day row whose end precedes its
 * start (23:30 → 07:30) runs to the axis bottom, so it really contends with a
 * late-night block instead of pretending to be empty (裁定 36.2).
 */
function effectiveEnd(startMinutes: number, endMinutes: null | number, spanEndMinutes: number): number {
  if (endMinutes === null) {
    return startMinutes
  }

  return endMinutes < startMinutes ? spanEndMinutes : endMinutes
}

interface OverlapCandidate {
  block: TimelineBlock
  /** Block end in minutes; open-ended → use the same value as start so it
   *  never overlaps anything on the right. */
  endMinutes: number
  /** Block start in minutes. */
  startMinutes: number
}

/**
 * Lay out one day. Returns null when nothing paints on `dayKey`.
 *
 * All blocks are laid out on ONE grid (裁定 36.2, "苹果那种"): a block that
 * collides with any other block moves to its own column. Blocks that collide
 * with nothing stay full width.
 */
export function layoutDay(
  events: AgendaEvent[],
  dayKey: string,
  avoidWindows: AgendaAvoidWindow[] = []
): TimelineDay | null {
  const eventRows = events
    .filter(event => dayKeyOf(event.start_at) === dayKey)
    .slice()
    .sort((left, right) => minutesOf(left.start_at) - minutesOf(right.start_at))

  if (eventRows.length === 0 && avoidWindows.length === 0) {
    return null
  }

  // 轴就是这一整天：00:00 → 24:00，逐小时打点。不再按当天内容伸缩——
  // 用户 2026-10-21 要的是「每天的时间轴从 0:00 开始算」，两天并排看时刻度也对得上。
  const spanStartMinutes = TIMELINE_START_HOUR * 60
  const spanEndMinutes = (TIMELINE_END_HOUR + 1) * 60

  const hours: number[] = []
  for (let hour = TIMELINE_START_HOUR; hour <= TIMELINE_END_HOUR; hour += 1) {
    hours.push(hour)
  }

  const eventCandidates: OverlapCandidate[] = eventRows.map(event => {
    const startMinutes = minutesOf(event.start_at)
    const endMinutes = event.end_at ? minutesOf(event.end_at) : null

    return {
      block: {
        column: 0,
        endClock: event.end_at ? clockOf(event.end_at) : null,
        heightPx: 0,
        id: event.id,
        location: event.location?.trim() || event.evidence?.location?.trim() || null,
        openEnded: endMinutes === null,
        overlaps: false,
        source: event,
        startMinutes,
        status: event.status,
        title: event.title,
        topPx: Math.round(pxOf(startMinutes, spanStartMinutes)),
        widthFraction: 0
      },
      endMinutes: effectiveEnd(startMinutes, endMinutes, spanEndMinutes),
      startMinutes
    }
  })

  // ONE grid (裁定 36.2): blocks that intersect get different columns instead
  // of stacking as two translucent layers. Blocks that collide with nothing
  // keep the full width.
  const grid = [...eventCandidates].sort(
    (left, right) => left.startMinutes - right.startMinutes
  )

  assignOverlapColumns(grid)

  const columnCounts = clusterColumnCounts(grid)

  for (const candidate of grid) {
    // 没有结束时间的行 `endMinutes === startMinutes` ⇒ 高度天然是 0：它就是一条线，
    // 不是一块压着别人的矩形。
    candidate.block.heightPx = blockHeightPx(
      candidate.startMinutes,
      candidate.endMinutes,
      spanEndMinutes
    )
    candidate.block.widthFraction = 1 / (columnCounts.get(candidate) ?? 1)
  }

  // Confirmed avoid windows (裁定 39 / WP-AXIS-TODAY). Same cross-midnight
  // rule as events. Drawn behind everything, untitled, full-width.
  const avoidBands: TimelineAvoidBand[] = avoidWindows
    .filter(window => dayKeyOf(window.start_at) === dayKey)
    .map(window => {
      const startMinutes = minutesOf(window.start_at)
      const endMinutes = minutesOf(window.end_at)
      const topPx = Math.round(pxOf(startMinutes, spanStartMinutes))
      const heightPx = Math.round(
        ((Math.max(startMinutes, Math.min(endMinutes, spanEndMinutes)) - startMinutes) / 60) * HOUR_HEIGHT
      )

      return { heightPx, topPx }
    })

  const result: TimelineDay = {
    avoidBands,
    axisContentFraction: 1 - LABEL_GUTTER_FRACTION,
    blocks: eventCandidates.map(candidate => candidate.block),
    hours,
    key: dayKey,
    spanEndMinutes,
    spanStartMinutes,
    totalHeightPx: Math.round(pxOf(spanEndMinutes, spanStartMinutes))
  }

  return result
}

/**
 * Greedy interval-graph colouring: a block reuses the first column that is free
 * by its start, otherwise it opens a new one.
 *
 * `overlaps` is then decided per block by REAL interval intersection, not by
 * "this lane ever used a second column" — the old shortcut painted every block
 * of the day amber as soon as two of them collided (裁定 32.1). Open-ended
 * blocks carry `endMinutes === startMinutes`, so they never mark a neighbour
 * (they'd otherwise swallow a column and tint the whole axis).
 */
function assignOverlapColumns(candidates: OverlapCandidate[]): void {
  const columnEnds: number[] = []

  for (const candidate of candidates) {
    let column = columnEnds.findIndex(end => end <= candidate.startMinutes)

    if (column === -1) {
      column = columnEnds.length
      columnEnds.push(candidate.endMinutes)
    } else {
      columnEnds[column] = candidate.endMinutes
    }

    candidate.block.column = column
  }

  for (const candidate of candidates) {
    candidate.block.overlaps = candidates.some(
      other =>
        other !== candidate &&
        other.startMinutes < candidate.endMinutes &&
        candidate.startMinutes < other.endMinutes
    )
  }
}

/**
 * Column count per overlap cluster.
 *
 * One grid holds every lane (裁定 36.2 "苹果那种"): a block only splits the axis
 * width with blocks it can actually collide with — transitively — so a morning
 * clash never shrinks an unrelated evening block. Clusters are the connected
 * components of the interval-overlap graph, found by a single sweep over the
 * start-sorted grid (a zero-length open-ended row joins a cluster it starts
 * inside but never extends it).
 */
function clusterColumnCounts(candidates: OverlapCandidate[]): Map<OverlapCandidate, number> {
  const counts = new Map<OverlapCandidate, number>()
  let cluster: OverlapCandidate[] = []
  let clusterEnd = Number.NEGATIVE_INFINITY

  const closeCluster = () => {
    if (cluster.length === 0) {
      return
    }

    const widest = Math.max(...cluster.map(candidate => candidate.block.column)) + 1

    for (const candidate of cluster) {
      counts.set(candidate, widest)
    }

    cluster = []
  }

  for (const candidate of candidates) {
    if (cluster.length > 0 && candidate.startMinutes >= clusterEnd) {
      closeCluster()
      clusterEnd = Number.NEGATIVE_INFINITY
    }

    cluster.push(candidate)
    clusterEnd = Math.max(clusterEnd, candidate.endMinutes)
  }

  closeCluster()

  return counts
}

/** Day keys that carry events, chronological. */
export function dayKeysOf(events: AgendaEvent[]): string[] {
  return [...new Set(events.map(event => dayKeyOf(event.start_at)).filter(Boolean))].sort()
}
