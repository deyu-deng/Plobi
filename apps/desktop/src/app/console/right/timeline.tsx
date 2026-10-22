/**
 * Agenda timeline (U3, spec §3.3) — the L1 right rail's day view (R-009 / R-020 / R-031.1).
 *
 * Rendered as a **vertical day axis**: hour ticks from 08:00 to 23:00 (widened
 * when the day holds anything outside that window). Each block sits between
 * its `start_at` and `end_at`; side-by-side overlaps (裁定 31.1 / 36.2, "苹果
 * 那种") get their own column — across ALL lanes, not per lane — so a class and
 * a breakfast that collide sit next to each other instead of stacking as two
 * translucent layers. The lane only decides the skin (裁定 34.3): solid for
 * events, dashed for routine anchors, dotted + accent rail for planned work.
 * Open-ended blocks stay honest — minimum height and `未写结束`, never a
 * fabricated hour. Today's axis also draws a "now" line at the local clock.
 *
 * Layout math lives in `./timeline-layout` (pure, unit-tested); this file only
 * renders.
 */

import { useEffect, useMemo, useRef, useState } from 'react'

import { PanelSectionLabel } from '@/app/overlays/panel'
import { currentI18nLocale, useI18n } from '@/i18n'
import { cn } from '@/lib/utils'
import type { AgendaAnchor, AgendaAvoidWindow, AgendaEvent, AgendaPlanItem } from '@/types/plobi'

import {
  type RailDay,
  shiftDateKey,
  useAgendaRailDay,
  useAgendaRailWeekCounts,
  weekKeysOf
} from './use-agenda-timeline'

import {
  emptyDay,
  HOUR_HEIGHT,
  LABEL_GUTTER_FRACTION,
  LANE_INSET_PX,
  layoutDay,
  type TimelineAvoidBand,
  type TimelineBlock,
  type TimelineDay
} from './timeline-layout'

function localDateKey(date: Date): string {
  const pad = (value: number) => String(value).padStart(2, '0')

  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
}

function padHour(hour: number): string {
  return `${String(hour).padStart(2, '0')}:00`
}

/**
 * 抬头上那串「几号 · 星期几」。Apple 日历那一族的抬头不只有"今天"——光看
 * 「今天／明天」分不清这一轴是几号，跨零点尤其看不出来。跟着界面语言走，
 * 不自己写死四种语言的星期名。
 */
function dateHeadingOf(dayKey: string): string {
  const date = new Date(`${dayKey}T00:00:00`)

  if (Number.isNaN(date.getTime())) {
    return dayKey
  }

  return new Intl.DateTimeFormat(currentI18nLocale(), {
    day: '2-digit',
    month: '2-digit',
    weekday: 'short'
  }).format(date)
}

/**
 * Lane skin for one block. Three lanes share the axis, so the stroke style
 * carries the identity — at a glance you must be able to tell "this is a class
 * / a hand-written event" from "this is my routine" from "this is planned work"
 * (裁定 34.3). Only existing UI vars, no new design language:
 *   - event  → solid border, tinted by status (unchanged);
 *   - anchor → dashed hairline + faint sky wash = the day's background band;
 *   - plan   → dotted hairline + a thick `--ui-accent` left rail (the same
 *     accent rail `pending-event-card` uses for "this is a proposal").
 */
function blockBorderClass(block: TimelineBlock): string {
  if (block.lane === 'anchor') {
    return 'border-dashed border-sky-500/40 bg-sky-500/5'
  }

  if (block.lane === 'plan') {
    return 'border-dotted border-(--ui-stroke-tertiary) border-l-2 border-l-(--ui-accent) bg-(--ui-accent)/5'
  }

  // events (lane === 'event')
  if (block.overlaps) {
    return 'border-amber-500/70 bg-amber-500/10'
  }

  if (block.status === 'pending') {
    return 'border-amber-500/50 bg-amber-500/10'
  }

  if (block.status === 'cancelled') {
    return 'border-border/60 bg-muted/40 opacity-70'
  }

  return 'border-emerald-600/40 bg-emerald-500/5'
}

function DayAxis({
  avoidBands,
  containerRef,
  day,
  label,
  nowMinutes,
  pendingLabel
}: {
  avoidBands: TimelineAvoidBand[]
  /** Forwarded so the scroll-into-view effect can target a single axis. */
  containerRef?: (node: HTMLDivElement | null) => void
  day: TimelineDay
  label: string
  /** Local wall-clock minutes since midnight, only for the LOCAL today axis. */
  nowMinutes?: number
  pendingLabel: string
}) {
  const axisLeftFraction = LABEL_GUTTER_FRACTION
  // Same minute → px mapping as the ticks and blocks (裁定 32.1's `pxOf`), so
  // the "now" line lands exactly on the hour mark it belongs to.
  const nowTopPx =
    nowMinutes === undefined ? null : Math.round(((nowMinutes - day.spanStartMinutes) / 60) * HOUR_HEIGHT)
  const showNow =
    nowTopPx !== null && nowMinutes !== undefined && nowMinutes >= day.spanStartMinutes && nowMinutes <= day.spanEndMinutes

  return (
    <div
      className="flex flex-col gap-1"
      data-testid={`timeline-day-${day.key}`}
      ref={containerRef}
    >
      <div className="flex items-baseline justify-between gap-2">
        <PanelSectionLabel>{label}</PanelSectionLabel>
        <span
          className="shrink-0 tabular-nums text-[0.62rem] text-(--ui-text-tertiary)"
          data-testid={`timeline-date-${day.key}`}
        >
          {dateHeadingOf(day.key)}
        </span>
      </div>
      <div className="relative" style={{ height: day.totalHeightPx }}>
        {day.hours.map(hour => (
          <div
            className="pointer-events-none absolute inset-x-0 flex items-center gap-1.5"
            data-testid={`timeline-tick-${hour}`}
            key={hour}
            style={{ top: ((hour * 60 - day.spanStartMinutes) / 60) * HOUR_HEIGHT }}
          >
            <span className="w-9 shrink-0 tabular-nums text-[0.62rem] text-(--ui-text-tertiary)">
              {padHour(hour)}
            </span>
            <span className="h-px flex-1 bg-border/60" />
          </div>
        ))}

        {showNow && (
          // 裁定 40: the "now" line MUST stand out from the hour grid. The
          // old 1px secondary stroke was indistinguishable from the hour
          // ticks once the rail scrolled past noon; we now ship it as an
          // accent bar with a leading dot. Aria-hidden still — visual only.
          <div
            aria-hidden="true"
            className="pointer-events-none absolute inset-x-0 z-20 flex items-center"
            data-testid="timeline-now"
            style={{ top: nowTopPx ?? 0 }}
          >
            <span className="-translate-y-1/2 inline-block size-1.5 rounded-full bg-(--ui-accent) shadow-[0_0_0.375rem_color-mix(in_srgb,var(--ui-accent)_55%,transparent)]" />
            <span className="block h-[2px] flex-1 bg-(--ui-accent)" />
          </div>
        )}

        {avoidBands.map((band, index) => (
          // Faint full-width bands behind blocks; no title, no interaction.
          // Same top/height pipeline as the blocks, no column math.
          <div
            aria-hidden="true"
            className="pointer-events-none absolute inset-x-0 z-0 h-2 rounded-sm bg-(--ui-accent)/8"
            data-testid={`timeline-avoid-${index}`}
            key={`avoid-${index}-${band.topPx}`}
            style={{ top: band.topPx, height: band.heightPx }}
          />
        ))}

        {day.blocks.map((block, index) => {
          // Side-by-side: each block takes `widthFraction` (always `1 / N`
          // where N is the columnCount for this block's lane) of the axis
          // content area. The left edge sits at the column boundary, never
          // an arbitrary pixel offset.
          const axisLeftPct = axisLeftFraction * 100
          const widthPct = day.axisContentFraction * block.widthFraction * 100
          const leftPct = axisLeftPct + block.column * widthPct
          const widthPx = `calc(${widthPct}% - ${LANE_INSET_PX}px)`
          const leftPx = `calc(${leftPct}% + ${LANE_INSET_PX}px)`

          if (block.openEnded) {
            // 没有结束时间＝那一刻，不是一段。画成**横贯当天的一条线**、标题挂在线上
            // （Apple 日历里 deadline 就是这个形状）。它以前是一块 22px 的最小高度矩形，
            // 于是 13:13 那条直接压在 13:25 的课上——线不参与列切分，也不遮任何人。
            return (
              <div
                className="absolute inset-x-0 z-10 flex items-center gap-1.5"
                data-lane={block.lane}
                data-open-ended="true"
                data-testid={`timeline-block-${block.id ?? `deadline-${index}`}`}
                key={block.id ?? `deadline-${index}-${block.startMinutes}`}
                style={{ top: block.topPx }}
              >
                <span className="w-9 shrink-0 tabular-nums text-[0.62rem] text-(--ui-accent)">
                  {clockOf(block.source && 'start_at' in block.source ? block.source.start_at : '')}
                </span>
                <span className="size-1.5 shrink-0 rotate-45 border border-(--ui-accent) bg-(--ui-accent)/30" />
                <span className="min-w-0 max-w-[42%] truncate text-[0.72rem] text-foreground/90">{block.title}</span>
                {block.status === 'pending' ? (
                  <span className="shrink-0 text-[0.62rem] text-amber-600/90">{pendingLabel}</span>
                ) : null}
                <span className="h-px min-w-0 flex-1 bg-(--ui-accent)/45" />
              </div>
            )
          }

          return (
            <div
              className={cn(
                'absolute overflow-hidden rounded-md border px-1.5 py-0.5',
                // Routine anchors/plans are the day's soft background band; the
                // real events must never be covered by one (裁定 32.1 独立 lane).
                block.lane === 'event' ? 'z-10' : 'z-0',
                blockBorderClass(block)
              )}
              data-lane={block.lane}
              data-overlap={block.overlaps ? 'true' : undefined}
              data-testid={`timeline-block-${block.id ?? `anchor-${index}`}`}
              key={block.id ?? `anchor-${index}-${block.startMinutes}`}
              style={{
                // `top: auto` (the default for an absolutely positioned box)
                // pins every block to the container top — the "everything
                // stacked at 06:00" bug. Always place by the computed offset.
                top: block.topPx,
                // 该多长就多长：短会就是窄的一条，不为了塞字放大它。
                height: block.heightPx,
                left: leftPx,
                width: widthPx
              }}
            >
              <div className="flex min-w-0 items-center gap-1">
                <span className="shrink-0 tabular-nums text-[0.62rem] text-(--ui-text-tertiary)">
                  {clockOf(block.source && 'start_at' in block.source ? block.source.start_at : '')}
                  {block.endClock ? `–${block.endClock}` : ''}
                </span>
                {block.lane === 'event' && block.status === 'pending' ? (
                  <span className="shrink-0 text-[0.62rem] text-amber-600/90">{pendingLabel}</span>
                ) : null}
                <span className="min-w-0 truncate text-[0.72rem] text-foreground/90">{block.title}</span>
              </div>
              {block.location ? (
                <span className="block truncate text-[0.6rem] text-(--ui-text-tertiary)">{block.location}</span>
              ) : null}
            </div>
          )
        })}
      </div>
    </div>
  )
}

export function Timeline({
  anchors,
  avoidWindows,
  events,
  plans
}: {
  anchors?: AgendaAnchor[]
  /** Confirmed avoid windows keyed by day (rendered as faint bands). */
  avoidWindows?: AgendaAvoidWindow[]
  events: AgendaEvent[]
  plans?: AgendaPlanItem[]
}) {
  const { t } = useI18n()
  const todayKey = localDateKey(new Date())
  const tomorrowDate = new Date()
  tomorrowDate.setDate(tomorrowDate.getDate() + 1)
  const tomorrowKey = localDateKey(tomorrowDate)
  // Local wall clock, read once per render — the "now" line is a TODAY-only
  // affordance (裁定 36.2), so tomorrow's axis never draws one.
  const now = new Date()
  const nowMinutes = now.getHours() * 60 + now.getMinutes()
  const todayAxisRef = useRef<HTMLDivElement | null>(null)

  // 裁定 40: scroll today into view when the rail first mounts (or when the
  // user lands on a date that is NOT today), so the "now" line lands in the
  // visible band of the right rail. Avoids the previous behaviour where
  // overflow-y-auto on a tall axis left the rail anchored at the top and
  // the now line off-screen below noon.
  useEffect(() => {
    if (typeof window === 'undefined') {
      return
    }

    const axis = todayAxisRef.current

    if (!axis) {
      return
    }

    const scroller = closestScrollableAncestor(axis)

    if (!scroller) {
      // No scrollable parent → caller is showing the whole axis inline,
      // nothing to do.
      return
    }

    const axisRect = axis.getBoundingClientRect()
    const scrollerRect = scroller.getBoundingClientRect()

    if (axisRect.top >= scrollerRect.top && axisRect.bottom <= scrollerRect.bottom) {
      return
    }

    const offsetWithinScroller = axisRect.top - scrollerRect.top + scroller.scrollTop
    // Centre the axis vertically inside the scroller; the rail is narrow
    // enough that "top of axis at scrollTop" works equally well.
    scroller.scrollTop = Math.max(0, offsetWithinScroller - 16)
  }, [todayKey])

  // Defensive dedupe: any upstream layer that appends the same event/anchor
  // twice (e.g. day endpoint returning today's block on top of the all-events
  // window) would otherwise make `layoutDay` greedy-split the duplicate into
  // its own column, so the same class would render at half-width on the right
  // of its real column (WP-AXIS-INSIGHT). We dedupe by id when present, and
  // by `(start_at, title)` for id-less rows (anchors / plans / avoid windows).
  // The first occurrence wins; ordering is preserved.
  function dedupe<T extends { id?: null | string; start_at: string; title?: string }>(rows: T[]): T[] {
    const seenIds = new Set<string>()
    const seenStart = new Set<string>()
    const out: T[] = []
    for (const row of rows) {
      if (row.id) {
        if (seenIds.has(row.id)) {
          continue
        }
        seenIds.add(row.id)
      } else {
        const key = `${row.start_at}|${row.title ?? ''}`
        if (seenStart.has(key)) {
          continue
        }
        seenStart.add(key)
      }
      out.push(row)
    }

    return out
  }

  // 一屏只画一天（WP-AGENDA-RAIL-APPLE）：抬头那一周就是换天的入口，
  // 左右箭头换的是**同一星期几的上一周／下一周**。
  const [selected, setSelected] = useState(todayKey)

  const seed: RailDay = useMemo(
    () => ({
      anchors: dedupe(anchors ?? []),
      avoidWindows: dedupe(avoidWindows ?? []),
      events: dedupe(events),
      plans: dedupe(plans ?? [])
    }),
    [events, anchors, plans, avoidWindows]
  )

  const rail = useAgendaRailDay(selected, seed, [todayKey, tomorrowKey])
  const weekCounts = useAgendaRailWeekCounts(selected, rail.events)
  const week = weekKeysOf(selected)

  const day = useMemo(
    () => layoutDay(rail.events, selected, rail.anchors, rail.plans, rail.avoidWindows) ?? emptyDay(selected),
    [rail, selected]
  )

  return (
    <div className="flex flex-col gap-3 px-1">
      <WeekStrip
        onShiftWeek={delta => setSelected(current => shiftDateKey(current, delta))}
        onSelect={setSelected}
        selected={selected}
        todayKey={todayKey}
        week={week}
        weekCounts={weekCounts}
      />

      {day.blocks.length === 0 ? (
        <p className="px-2 py-1 text-[0.72rem] text-(--ui-text-tertiary)" data-testid="timeline-empty">
          {t.console.timeline.emptyDesc}
        </p>
      ) : null}

      <DayAxis
        avoidBands={day.avoidBands ?? []}
        containerRef={node => {
          todayAxisRef.current = node
        }}
        day={day}
        label={
          selected === todayKey
            ? t.console.timeline.today
            : selected === tomorrowKey
              ? t.console.timeline.tomorrow
              : selected
        }
        nowMinutes={selected === todayKey ? nowMinutes : undefined}
        pendingLabel={t.console.timeline.pending}
      />
    </div>
  )
}

const WEEKDAY_NARROW = ['日', '一', '二', '三', '四', '五', '六']

function weekdayNarrow(index: number): string {
  try {
    // 2024-01-07 是周日，按 index 推到对应那天；跟着界面语言走，不写死四语。
    return new Intl.DateTimeFormat(currentI18nLocale(), { weekday: 'narrow' }).format(
      new Date(2024, 0, 7 + index)
    )
  } catch {
    return WEEKDAY_NARROW[index]
  }
}

function ChevronButton({
  direction,
  label,
  onClick
}: {
  direction: 'left' | 'right'
  label: string
  onClick: () => void
}) {
  return (
    <button
      aria-label={label}
      className="flex h-7 w-6 shrink-0 items-center justify-center rounded-md text-(--ui-text-secondary) hover:bg-(--ui-accent)/10"
      data-testid={`timeline-week-${direction}`}
      onClick={onClick}
      title={label}
      type="button"
    >
      <svg aria-hidden="true" className="size-3.5" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8} viewBox="0 0 16 16">
        {direction === 'left' ? <path d="M10 3 5 8l5 5" /> : <path d="M6 3l5 5-5 5" />}
      </svg>
    </button>
  )
}

/**
 * 抬头那一周：号码就是日期，下面那几点就是"那天有没有事"。
 * 选中的那天填 accent 圆底，今天没被选中时留一圈描边——不写「今天」两个字也认得出。
 */
function WeekStrip({
  onShiftWeek,
  onSelect,
  selected,
  todayKey,
  week,
  weekCounts
}: {
  onShiftWeek: (deltaDays: number) => void
  onSelect: (dateKey: string) => void
  selected: string
  todayKey: string
  week: string[]
  weekCounts: Record<string, number>
}) {
  const todayIndex = new Date(`${todayKey}T00:00:00`).getDay()

  return (
    <div className="flex items-center gap-1">
      <ChevronButton direction="left" label={shiftDateKey(selected, -7)} onClick={() => onShiftWeek(-7)} />

      <div className="flex flex-1 items-start justify-between">
        {week.map((key, index) => {
          const active = key === selected
          const isToday = key === todayKey
          const count = weekCounts[key] ?? 0

          return (
            <button
              className="flex w-8 flex-col items-center gap-1 rounded-md py-0.5 hover:bg-(--ui-accent)/8"
              data-testid={`timeline-day-cell-${key}`}
              key={key}
              onClick={() => onSelect(key)}
              title={key}
              type="button"
            >
              <span
                className={cn(
                  'text-[0.6rem] leading-none',
                  index === todayIndex ? 'text-foreground/90' : 'text-(--ui-text-tertiary)'
                )}
              >
                {weekdayNarrow(index)}
              </span>
              <span
                className={cn(
                  'flex size-6 items-center justify-center rounded-full text-[0.78rem] tabular-nums',
                  active
                    ? 'bg-(--ui-accent) font-semibold text-(--ui-text-on-accent)'
                    : count === 0
                      ? 'text-(--ui-text-quaternary)'
                      : 'text-foreground/90',
                  isToday && !active && 'ring-1 ring-(--ui-accent)'
                )}
              >
                {Number(key.slice(8, 10))}
              </span>
              <span className="flex h-1 items-center gap-0.5">
                {Array.from({ length: Math.min(count, 3) }, (_, dot) => (
                  <span
                    className={cn('size-[3px] rounded-full', active ? 'bg-(--ui-text-on-accent)/80' : 'bg-(--ui-accent)')}
                    key={dot}
                  />
                ))}
              </span>
            </button>
          )
        })}
      </div>

      <ChevronButton direction="right" label={shiftDateKey(selected, 7)} onClick={() => onShiftWeek(7)} />
    </div>
  )
}

function clockOf(iso: string): string {
  return iso && iso.length >= 16 ? iso.slice(11, 16) : iso
}

/**
 * Walk up to the nearest ancestor whose computed `overflow-y` is auto / scroll
 * / overlay. The right rail sits inside multiple nested Pane shells; querying
 * the inline container alone would miss the parent scroller that actually
 * moves when the user scrolls.
 */
function closestScrollableAncestor(node: HTMLElement): HTMLElement | null {
  let current: HTMLElement | null = node.parentElement

  while (current && current !== document.body) {
    const overflow = window.getComputedStyle(current).overflowY

    if (overflow === 'auto' || overflow === 'scroll' || overflow === 'overlay') {
      return current
    }

    current = current.parentElement
  }

  return null
}
