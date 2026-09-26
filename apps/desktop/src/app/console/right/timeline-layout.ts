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
 * Three block lanes share the same axis:
 *   - `event` — the real agenda rows (the user's meetings / events);
 *   - `anchor` — the day's routine blocks (sleep / meals) shown as a soft
 *     background band; never actionable;
 *   - `plan` — planned project work, drawn in the same anchor lane.
 *
 * Rules that come straight from the rulings:
 * - The axis always shows at least 08:00 → 23:00 and stretches to fit anything
 *   outside that window, so an early or late entry is never clipped away.
 * - A block's height follows `start_at` → `end_at`. A missing `end_at` is NOT
 *   stretched into a fake hour: it gets the minimum height and is flagged
 *   `openEnded` so the UI can say「未写结束」.
 * - Side-by-side overlaps (裁定 31.1 / 36.2): all lanes share one grid, so
 *   blocks that collide in time — even across lanes — split into columns and
 *   each takes `1 / columnCount` of the axis width, where columnCount is the
 *   widest column of that block's own overlap cluster. Only the blocks whose
 *   intervals actually intersect are flagged `overlaps`. Open-ended blocks do
 *   NOT participate in the overlap test — they'd swallow a column.
 * - A block's vertical position is `topPx` (px from the axis top); the renderer
 *   MUST apply it, otherwise every block stacks at the top (裁定 32.1).
 */

import type { AgendaAnchor, AgendaAvoidWindow, AgendaEvent, AgendaPlanItem } from '@/types/plobi'

export const TIMELINE_START_HOUR = 8
export const TIMELINE_END_HOUR = 23

/** Pixels per hour on the day axis. */
export const HOUR_HEIGHT = 44
/** Minimum block height so a zero-length (or open-ended) entry stays readable. */
export const MIN_BLOCK_HEIGHT = 22
/** Horizontal width fraction reserved for the hour-label gutter. */
export const LABEL_GUTTER_FRACTION = 0.18
/** Outer axis padding (px) so the leftmost column has breathing room. */
export const LANE_INSET_PX = 6

export type TimelineLane = 'anchor' | 'event' | 'plan'

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
  /** Block kind on the axis. */
  lane: TimelineLane
  /** True when this block shares its column slice with another at the same time. */
  overlaps: boolean
  /** `end_at` is missing (never guessed) — width is the minimum, no overlap test. */
  openEnded: boolean
  /** The source row: an agenda event, an anchor, or a plan item. */
  source: AgendaAnchor | AgendaEvent | AgendaPlanItem
  /** Stable id from `source.id` when present (anchors have none). */
  id: null | string
  /** Title text to render in the block. */
  title: string
  /** `status` only present when `source` is an AgendaEvent. */
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
 * A minimum day axis: the default 08:00–23:00 ticks and a flat height. Used when
 * today carries no events, anchors, plans, or avoid windows — the rail still
 * paints an empty "today" axis above tomorrow (裁定 37.2 / 39), so the user
 * can see the now line and the day's structure.
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
 * A same-day row whose end precedes its start (e.g. a sleep anchor written as
 * `23:30 → 07:30` on one date) crosses midnight: it is painted down to the
 * axis bottom rather than producing a negative height (裁定 32.1). Open-ended
 * rows keep the honest minimum — never a fabricated hour.
 */
function blockHeightPx(
  startMinutes: number,
  endMinutes: number,
  spanEndMinutes: number,
  openEnded: boolean
): number {
  if (openEnded) {
    return MIN_BLOCK_HEIGHT
  }

  const clampedEnd = endMinutes < startMinutes ? spanEndMinutes : endMinutes
  const naturalHeight = ((clampedEnd - startMinutes) / 60) * HOUR_HEIGHT

  return Math.max(MIN_BLOCK_HEIGHT, Math.round(naturalHeight))
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
 * All three lanes are laid out on ONE grid (裁定 36.2, "苹果那种"): a block that
 * collides with any other block — whatever its lane — moves to its own column.
 * Blocks that collide with nothing stay full width. The lane still decides the
 * skin (`data-lane` + the stroke classes), not the column band.
 */
export function layoutDay(
  events: AgendaEvent[],
  dayKey: string,
  anchors: AgendaAnchor[] = [],
  plans: AgendaPlanItem[] = [],
  avoidWindows: AgendaAvoidWindow[] = []
): TimelineDay | null {
  const eventRows = events
    .filter(event => dayKeyOf(event.start_at) === dayKey)
    .slice()
    .sort((left, right) => minutesOf(left.start_at) - minutesOf(right.start_at))

  const anchorRows = anchors.filter(row => dayKeyOf(row.start_at) === dayKey)
  const planRows = plans
    .filter(row => dayKeyOf(row.start_at) === dayKey)
    .slice()
    .sort((left, right) => minutesOf(left.start_at) - minutesOf(right.start_at))

  if (eventRows.length === 0 && anchorRows.length === 0 && planRows.length === 0 && avoidWindows.length === 0) {
    return null
  }

  const starts: number[] = []
  const ends: number[] = []

  for (const event of eventRows) {
    starts.push(minutesOf(event.start_at))
    ends.push(event.end_at ? minutesOf(event.end_at) : minutesOf(event.start_at))
  }

  for (const anchor of anchorRows) {
    starts.push(minutesOf(anchor.start_at))
    ends.push(minutesOf(anchor.end_at))
  }

  for (const plan of planRows) {
    starts.push(minutesOf(plan.start_at))
    ends.push(plan.end_at ? minutesOf(plan.end_at) : minutesOf(plan.start_at))
  }

  // include avoid windows in the axis span only when the day has any of them
  // (empty avoid arrays would otherwise push the floor to ±Infinity).
  const avoidStartMinutes = avoidWindows.map(window => minutesOf(window.start_at))
  const avoidEndMinutes = avoidWindows.map(window => minutesOf(window.end_at))
  const spansMinStart = starts.length === 0 && avoidStartMinutes.length === 0
    ? [TIMELINE_START_HOUR * 60]
    : [...starts, ...avoidStartMinutes]
  const spansMaxEnd = ends.length === 0 && avoidEndMinutes.length === 0
    ? [TIMELINE_END_HOUR * 60]
    : [...ends, ...avoidEndMinutes]

  const minMinutes = Math.min(...spansMinStart)
  const maxMinutes = Math.max(...spansMaxEnd)

  // At least 08:00–23:00; widen to whatever the day actually contains.
  const spanStartMinutes = Math.min(TIMELINE_START_HOUR * 60, Math.floor(minMinutes / 60) * 60)
  const spanEndMinutes = Math.max((TIMELINE_END_HOUR + 1) * 60, Math.ceil(maxMinutes / 60) * 60)

  // Ticks are hour marks *inside* the span: 08:00–23:00 by default, so the
  // default axis never shows a "24:00" label.
  const hours: number[] = []
  for (let hour = spanStartMinutes / 60; hour * 60 < spanEndMinutes; hour += 1) {
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
        lane: 'event' as TimelineLane,
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

  const anchorCandidates: OverlapCandidate[] = anchorRows.map(anchor => {
    const startMinutes = minutesOf(anchor.start_at)

    return {
      block: {
        column: 0,
        endClock: clockOf(anchor.end_at),
        heightPx: 0,
        id: null,
        lane: 'anchor' as TimelineLane,
        location: null,
        openEnded: false,
        overlaps: false,
        source: anchor,
        startMinutes,
        status: null,
        title: anchor.label,
        topPx: Math.round(pxOf(startMinutes, spanStartMinutes)),
        widthFraction: 0
      },
      endMinutes: effectiveEnd(startMinutes, minutesOf(anchor.end_at), spanEndMinutes),
      startMinutes
    }
  })

  const planCandidates: OverlapCandidate[] = planRows.map(plan => {
    const startMinutes = minutesOf(plan.start_at)
    const endMinutes = plan.end_at ? minutesOf(plan.end_at) : null

    return {
      block: {
        column: 0,
        endClock: plan.end_at ? clockOf(plan.end_at) : null,
        heightPx: 0,
        id: plan.id,
        lane: 'plan' as TimelineLane,
        location: null,
        openEnded: endMinutes === null,
        overlaps: false,
        source: plan,
        startMinutes,
        status: null,
        title: plan.title,
        topPx: Math.round(pxOf(startMinutes, spanStartMinutes)),
        widthFraction: 0
      },
      endMinutes: effectiveEnd(startMinutes, endMinutes, spanEndMinutes),
      startMinutes
    }
  })

  // ONE grid for every lane (裁定 36.2): a lecture and a breakfast that
  // intersect get different columns instead of stacking as two translucent
  // layers. Blocks that collide with nothing keep the full width.
  const grid = [...eventCandidates, ...anchorCandidates, ...planCandidates].sort(
    (left, right) => left.startMinutes - right.startMinutes
  )

  assignOverlapColumns(grid)

  const columnCounts = clusterColumnCounts(grid)

  for (const candidate of grid) {
    candidate.block.heightPx = blockHeightPx(
      candidate.startMinutes,
      candidate.endMinutes,
      spanEndMinutes,
      candidate.block.openEnded
    )
    candidate.block.widthFraction = 1 / (columnCounts.get(candidate) ?? 1)
  }

  // Confirmed avoid windows (裁定 39 / WP-AXIS-TODAY). Same cross-midnight
  // rule as anchors. Drawn behind everything, untitled, full-width.
  const avoidBands: TimelineAvoidBand[] = avoidWindows
    .filter(window => dayKeyOf(window.start_at) === dayKey)
    .map(window => {
      const startMinutes = minutesOf(window.start_at)
      const endMinutes = minutesOf(window.end_at)
      const topPx = Math.round(pxOf(startMinutes, spanStartMinutes))
      const heightPx = Math.max(
        MIN_BLOCK_HEIGHT,
        Math.round(((Math.max(startMinutes, Math.min(endMinutes, spanEndMinutes)) - startMinutes) / 60) * HOUR_HEIGHT)
      )

      return { heightPx, topPx }
    })

  const result: TimelineDay = {
    avoidBands,
    axisContentFraction: 1 - LABEL_GUTTER_FRACTION,
    // Lane order (events → routine) is presentation-only: the renderer's
    // z-index decides layering, and it keeps block test ids stable across the
    // one-grid merge.
    blocks: [
      ...eventCandidates.map(candidate => candidate.block),
      ...anchorCandidates.map(candidate => candidate.block),
      ...planCandidates.map(candidate => candidate.block)
    ],
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
