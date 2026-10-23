import { type ReactNode, useEffect, useMemo, useRef } from 'react'

import { Codicon } from '@/components/ui/codicon'
import { currentI18nLocale } from '@/i18n'
import type { Translations } from '@/i18n'
import { cn } from '@/lib/utils'
import type { AgendaEvent } from '@/types/plobi'

import {
  emptyDay,
  HOUR_HEIGHT,
  LABEL_GUTTER_FRACTION,
  layoutDay,
  type TimelineBlock
} from '../../console/right/timeline-layout'

import {
  CALENDAR_MODES,
  dayKeyOf,
  eventsByDay,
  isSameMonth,
  monthGrid,
  parseDayKey,
  stepAnchor,
  weekKeys,
  yearMonths
} from './calendar-model'
import type { CalendarMode } from './calendar-model'

const KIND_ICON: Record<string, string> = {
  class: 'book',
  ddl: 'flag',
  meeting: 'people',
  task: 'check'
}

const KIND_TONE: Record<string, string> = {
  class: 'border-sky-500/40 bg-sky-500/10 text-sky-700 dark:text-sky-300',
  ddl: 'border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-300',
  meeting: 'border-violet-500/40 bg-violet-500/10 text-violet-700 dark:text-violet-300',
  task: 'border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300'
}

const KIND_DOT: Record<string, string> = {
  class: 'bg-sky-500',
  ddl: 'bg-amber-500',
  meeting: 'bg-violet-500',
  task: 'bg-emerald-500'
}

function clockOf(iso: string): string {
  return iso && iso.length >= 16 ? iso.slice(11, 16) : iso
}

function dayNumber(key: string): number {
  return parseDayKey(key).getDate()
}

function weekdayLabels(locale: string): string[] {
  const formatter = new Intl.DateTimeFormat(locale, { weekday: 'short' })
  const base = new Date(2026, 0, 4) // a Sunday

  return Array.from({ length: 7 }, (_, index) =>
    formatter.format(new Date(base.getFullYear(), base.getMonth(), base.getDate() + index))
  )
}

function monthLabel(key: string, locale: string): string {
  return new Intl.DateTimeFormat(locale, { month: 'long' }).format(parseDayKey(key))
}

function titleFor(mode: CalendarMode, anchor: string, locale: string): string {
  const date = parseDayKey(anchor)

  if (mode === 'year') {
    return new Intl.DateTimeFormat(locale, { year: 'numeric' }).format(date)
  }

  if (mode === 'month') {
    return new Intl.DateTimeFormat(locale, { month: 'long', year: 'numeric' }).format(date)
  }

  if (mode === 'week') {
    const days = weekKeys(anchor)

    const short = (key: string) =>
      new Intl.DateTimeFormat(locale, { day: 'numeric', month: 'short' }).format(parseDayKey(key))

    return `${short(days[0])} – ${short(days[6])}`
  }

  return new Intl.DateTimeFormat(locale, {
    day: 'numeric',
    month: 'long',
    weekday: 'short',
    year: 'numeric'
  }).format(date)
}

function eventChipClass(event: AgendaEvent): string {
  if (event.status === 'pending') {
    return 'border-amber-500/50 bg-amber-500/10 text-amber-700 dark:text-amber-300'
  }

  if (event.status === 'cancelled') {
    return 'border-border/60 bg-muted/40 text-muted-foreground line-through'
  }

  return KIND_TONE[event.kind] ?? 'border-border bg-muted/40 text-foreground'
}

function ToolbarButton({
  children,
  label,
  onClick
}: {
  children: ReactNode
  label: string
  onClick: () => void
}) {
  return (
    <button
      aria-label={label}
      className="flex size-7 items-center justify-center rounded-md text-(--ui-text-secondary) transition-colors hover:bg-(--ui-surface-2) hover:text-foreground"
      onClick={onClick}
      title={label}
      type="button"
    >
      {children}
    </button>
  )
}

function YearView({
  anchor,
  days,
  locale,
  onOpenDay
}: {
  anchor: string
  days: Map<string, AgendaEvent[]>
  locale: string
  onOpenDay: (key: string) => void
}) {
  const today = dayKeyOf(new Date())

  return (
    <div className="grid grid-cols-2 gap-3 px-2 pb-4 min-[42rem]:grid-cols-3 min-[58rem]:grid-cols-4">
      {yearMonths(anchor).map(monthKey => (
        <section
          className="rounded-lg border border-(--ui-stroke-tertiary) bg-(--ui-surface)/40 p-2"
          data-testid={`year-month-${monthKey.slice(0, 7)}`}
          key={monthKey}
        >
          <header className="mb-1 px-0.5 text-[0.72rem] font-medium text-foreground">
            {monthLabel(monthKey, locale)}
          </header>
          <div className="grid grid-cols-7 gap-px text-center">
            {monthGrid(monthKey).map(key => {
              const rows = days.get(key) ?? []
              const outside = !isSameMonth(key, monthKey)

              return (
                <button
                  aria-label={key}
                  className={cn(
                    'relative flex h-6 flex-col items-center justify-center rounded text-[0.62rem] tabular-nums transition-colors hover:bg-(--ui-surface-2)',
                    outside ? 'text-(--ui-text-tertiary)/45' : 'text-(--ui-text-secondary)',
                    key === today && 'bg-(--ui-accent)/10 font-semibold text-(--ui-accent)'
                  )}
                  key={key}
                  onClick={() => onOpenDay(key)}
                  type="button"
                >
                  {dayNumber(key)}
                  {rows.length > 0 ? (
                    <span className="absolute bottom-0 flex gap-0.5">
                      {rows.slice(0, 3).map((event, index) => (
                        <span
                          className={cn('size-1 rounded-full', KIND_DOT[event.kind] ?? 'bg-muted-foreground')}
                          key={`${event.id}-${index}`}
                        />
                      ))}
                    </span>
                  ) : null}
                </button>
              )
            })}
          </div>
        </section>
      ))}
    </div>
  )
}

function MonthView({
  anchor,
  days,
  locale,
  onOpenDay,
  onSelectEvent,
  selectedId
}: {
  anchor: string
  days: Map<string, AgendaEvent[]>
  locale: string
  onOpenDay: (key: string) => void
  onSelectEvent: (id: string) => void
  selectedId?: null | string
}) {
  const today = dayKeyOf(new Date())
  const labels = weekdayLabels(locale)

  return (
    <div className="flex min-h-0 flex-1 flex-col px-2 pb-3">
      <div className="grid grid-cols-7 border-b border-(--ui-stroke-tertiary) text-center">
        {labels.map(label => (
          <span className="py-1 text-[0.62rem] font-medium text-(--ui-text-tertiary)" key={label}>
            {label}
          </span>
        ))}
      </div>
      <div className="grid min-h-0 flex-1 auto-rows-fr grid-cols-7 overflow-y-auto rounded-b-md border border-t-0 border-(--ui-stroke-tertiary)">
        {monthGrid(anchor).map(key => {
          const rows = days.get(key) ?? []
          const outside = !isSameMonth(key, anchor)

          return (
            <div
              className={cn(
                'flex min-h-[4.6rem] cursor-pointer flex-col gap-0.5 border-b border-r border-(--ui-stroke-tertiary)/70 p-1 transition-colors last:border-r-0 hover:bg-(--ui-surface-2)/60',
                outside && 'bg-(--ui-surface-2)/30 text-(--ui-text-tertiary)/50'
              )}
              data-testid={`month-day-${key}`}
              key={key}
              onClick={() => onOpenDay(key)}
              role="button"
              tabIndex={0}
            >
              <span
                className={cn(
                  'flex size-5 items-center justify-center self-start rounded-full text-[0.68rem] tabular-nums',
                  key === today && 'bg-(--ui-accent) font-semibold text-(--ui-accent-foreground, white)'
                )}
              >
                {dayNumber(key)}
              </span>
              <div className="flex min-w-0 flex-col gap-0.5">
                {rows.slice(0, 2).map(event => (
                  <button
                    className={cn(
                      'flex min-w-0 items-center gap-1 truncate rounded border px-1 py-px text-left text-[0.6rem]',
                      eventChipClass(event),
                      event.id === selectedId && 'ring-1 ring-(--ui-accent)'
                    )}
                    key={event.id}
                    onClick={clickEvent => {
                      clickEvent.stopPropagation()
                      onSelectEvent(event.id)
                    }}
                    title={`${clockOf(event.start_at)} ${event.title}`}
                    type="button"
                  >
                    <Codicon name={KIND_ICON[event.kind] ?? 'circle-filled'} size="0.62rem" />
                    <span className="truncate">{event.title}</span>
                  </button>
                ))}
                {rows.length > 2 ? (
                  <span className="pl-0.5 text-[0.58rem] text-(--ui-text-tertiary)">
                    +{rows.length - 2}
                  </span>
                ) : null}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

function TimeBlock({
  block,
  onSelect
}: {
  block: TimelineBlock
  onSelect: (id: string) => void
}) {
  const event = block.source

  const style = {
    height: block.heightPx,
    left: `calc(${block.column * block.widthFraction * 100}% + ${block.column === 0 ? 2 : 1}px)`,
    top: block.topPx,
    width: `calc(${block.widthFraction * 100}% - 2px)`
  }

  if (block.openEnded) {
    return (
      <button
        className="absolute inset-x-1 z-10 flex items-center gap-1 overflow-hidden text-left"
        data-testid={`calendar-block-${block.id ?? 'open'}`}
        onClick={() => block.id && onSelect(block.id)}
        style={{ top: block.topPx }}
        title={`${clockOf(event.start_at)} ${event.title}`}
        type="button"
      >
        <span className="size-1.5 shrink-0 rotate-45 border border-amber-500 bg-amber-500/30" />
        <span className="shrink-0 text-[0.6rem] tabular-nums text-amber-700 dark:text-amber-300">
          {clockOf(event.start_at)}
        </span>
        <span className="min-w-0 truncate text-[0.66rem] text-foreground">{event.title}</span>
        <span className="h-px min-w-0 flex-1 bg-amber-500/50" />
      </button>
    )
  }

  return (
    <button
      className={cn(
        'absolute z-10 overflow-hidden rounded border px-1 py-0.5 text-left',
        eventChipClass(event),
        block.overlaps && 'ring-1 ring-amber-500/80'
      )}
      data-testid={`calendar-block-${block.id ?? 'block'}`}
      onClick={() => block.id && onSelect(block.id)}
      style={style}
      type="button"
    >
      <span className="block truncate text-[0.58rem] tabular-nums opacity-80">
        {clockOf(event.start_at)}
        {event.end_at ? `–${clockOf(event.end_at)}` : ''}
      </span>
      <span className="block truncate text-[0.66rem] font-medium">{event.title}</span>
      {event.location?.trim() ? (
        <span className="block truncate text-[0.56rem] opacity-70">{event.location.trim()}</span>
      ) : null}
    </button>
  )
}

function TimeAxis({
  anchor,
  columns,
  days,
  mode,
  now,
  onOpenDay,
  onSelectEvent,
  t
}: {
  anchor: string
  columns: string[]
  days: Map<string, AgendaEvent[]>
  mode: CalendarMode
  now: Date
  onOpenDay: (key: string) => void
  onSelectEvent: (id: string) => void
  t: Translations['agenda']
}) {
  const locale = currentI18nLocale()
  const today = dayKeyOf(now)
  const scrollRef = useRef<HTMLDivElement | null>(null)
  const hours = Array.from({ length: 24 }, (_, hour) => hour)
  const labelWidth = `${LABEL_GUTTER_FRACTION * 100}%`
  const nowMinutes = now.getHours() * 60 + now.getMinutes()

  useEffect(() => {
    const scroller = scrollRef.current

    if (!scroller) {
      return
    }

    const target = Math.max(0, (nowMinutes - 60) / 60 * HOUR_HEIGHT)
    scroller.scrollTop = target
  }, [nowMinutes])

  return (
    <div className="flex min-h-0 flex-1 flex-col px-2 pb-3">
      <div className="grid border-b border-(--ui-stroke-tertiary)" style={{ gridTemplateColumns: `${labelWidth} repeat(${columns.length}, minmax(0, 1fr))` }}>
        <span />
        {columns.map(key => (
          <button
            className={cn(
              'border-l border-(--ui-stroke-tertiary)/70 px-1 py-1.5 text-center transition-colors hover:bg-(--ui-surface-2)',
              key === anchor && mode === 'day' && 'bg-(--ui-accent)/5'
            )}
            key={key}
            onClick={() => onOpenDay(key)}
            type="button"
          >
            <span className="block text-[0.6rem] text-(--ui-text-tertiary)">
              {new Intl.DateTimeFormat(locale, { weekday: 'short' }).format(parseDayKey(key))}
            </span>
            <span
              className={cn(
                'mx-auto mt-0.5 flex size-6 items-center justify-center rounded-full text-[0.7rem] tabular-nums',
                key === today && 'bg-(--ui-accent) font-semibold text-(--ui-accent-foreground, white)',
                key === anchor && mode === 'day' && key !== today && 'ring-1 ring-(--ui-accent)'
              )}
            >
              {dayNumber(key)}
            </span>
          </button>
        ))}
      </div>

      <div className="relative min-h-0 flex-1 overflow-y-auto" ref={scrollRef}>
        <div className="flex" style={{ height: 24 * HOUR_HEIGHT }}>
          <div className="relative shrink-0" style={{ width: labelWidth }}>
            {hours.map(hour => (
              <span
                className="absolute right-1.5 -translate-y-1/2 text-[0.58rem] tabular-nums text-(--ui-text-tertiary)"
                key={hour}
                style={{ top: hour * HOUR_HEIGHT }}
              >
                {String(hour).padStart(2, '0')}:00
              </span>
            ))}
          </div>

          <div className="relative min-w-0 flex-1">
            <div className="pointer-events-none absolute inset-0">
              {hours.map(hour => (
                <span
                  className="absolute inset-x-0 h-px bg-(--ui-stroke-tertiary)/60"
                  key={hour}
                  style={{ top: hour * HOUR_HEIGHT }}
                />
              ))}
            </div>

            <div
              className="absolute inset-0 grid"
              style={{ gridTemplateColumns: `repeat(${columns.length}, minmax(0, 1fr))` }}
            >
              {columns.map(key => {
                const day = layoutDay(days.get(key) ?? [], key) ?? emptyDay(key)
                const isToday = key === today

                return (
                  <div
                    className="relative h-full border-l border-(--ui-stroke-tertiary)/70"
                    data-testid={`calendar-column-${key}`}
                    key={key}
                  >
                    {isToday ? (
                      <span
                        className="pointer-events-none absolute inset-x-0 z-20 h-px bg-red-500/70"
                        style={{ top: (nowMinutes / 60) * HOUR_HEIGHT }}
                      />
                    ) : null}
                    {day.blocks.map(block => (
                      <TimeBlock block={block} key={block.id ?? `${block.startMinutes}-${block.title}`} onSelect={onSelectEvent} />
                    ))}
                    {day.blocks.length === 0 && columns.length > 1 ? (
                      <span className="absolute inset-x-1 top-2 truncate text-center text-[0.58rem] text-(--ui-text-tertiary)/60">
                        {t.calendar.noEvents}
                      </span>
                    ) : null}
                  </div>
                )
              })}
            </div>
          </div>
        </div>
      </div>

    </div>
  )
}

function MiniWeekRow({
  anchor,
  days,
  onOpenDay,
  today
}: {
  anchor: string
  days: Map<string, AgendaEvent[]>
  onOpenDay: (key: string) => void
  today: string
}) {
  const keys = weekKeys(anchor)
  const locale = currentI18nLocale()

  return (
    <div className="grid grid-cols-7 gap-1 px-2 pb-2">
      {keys.map(key => {
        const rows = days.get(key) ?? []

        return (
          <button
            className={cn(
              'flex flex-col items-center gap-0.5 rounded-md py-1 transition-colors hover:bg-(--ui-surface-2)',
              key === anchor && 'bg-(--ui-accent)/10'
            )}
            data-testid={`mini-week-${key}`}
            key={key}
            onClick={() => onOpenDay(key)}
            type="button"
          >
            <span className="text-[0.58rem] text-(--ui-text-tertiary)">
              {new Intl.DateTimeFormat(locale, { weekday: 'narrow' }).format(parseDayKey(key))}
            </span>
            <span
              className={cn(
                'flex size-6 items-center justify-center rounded-full text-[0.68rem] tabular-nums',
                key === today && 'bg-(--ui-accent) font-semibold text-(--ui-accent-foreground, white)'
              )}
            >
              {dayNumber(key)}
            </span>
            <span className="flex h-1 items-center gap-0.5">
              {rows.slice(0, 3).map((event, index) => (
                <span
                  className={cn('size-1 rounded-full', KIND_DOT[event.kind] ?? 'bg-muted-foreground')}
                  key={`${event.id}-${index}`}
                />
              ))}
            </span>
          </button>
        )
      })}
    </div>
  )
}

export function AgendaCalendar({
  anchor,
  events,
  importMeta,
  mode,
  now,
  onAnchorChange,
  onCreate,
  onImport,
  onModeChange,
  onSelectEvent,
  selectedId,
  t
}: {
  anchor: string
  events: AgendaEvent[]
  importMeta?: string
  mode: CalendarMode
  now: Date
  onAnchorChange: (key: string) => void
  onCreate: () => void
  onImport: () => void
  onModeChange: (mode: CalendarMode) => void
  onSelectEvent: (id: string) => void
  selectedId?: null | string
  t: Translations['agenda']
}) {
  const locale = currentI18nLocale()
  const days = useMemo(() => eventsByDay(events), [events])
  const today = dayKeyOf(now)
  const title = titleFor(mode, anchor, locale)
  const columns = mode === 'week' ? weekKeys(anchor) : mode === 'day' ? [anchor] : []

  const openDay = (key: string) => {
    onAnchorChange(key)
    onModeChange('day')
  }

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col">
      <div className="flex flex-wrap items-center gap-1 px-2 pb-2">
        <div className="flex items-center gap-0.5">
          <ToolbarButton label={t.calendar.prev} onClick={() => onAnchorChange(stepAnchor(mode, anchor, -1))}>
            <Codicon name="chevron-left" size="0.9rem" />
          </ToolbarButton>
          <ToolbarButton label={t.calendar.today} onClick={() => onAnchorChange(dayKeyOf(now))}>
            <Codicon name="calendar" size="0.9rem" />
          </ToolbarButton>
          <ToolbarButton label={t.calendar.next} onClick={() => onAnchorChange(stepAnchor(mode, anchor, 1))}>
            <Codicon name="chevron-right" size="0.9rem" />
          </ToolbarButton>
        </div>
        <h3 className="min-w-0 flex-1 truncate px-1 text-[0.82rem] font-semibold text-foreground">{title}</h3>
        <div className="flex items-center rounded-md border border-(--ui-stroke-tertiary) p-0.5">
          {CALENDAR_MODES.map(value => (
            <button
              aria-label={t.calendar.modes[value]}
              className={cn(
                'rounded px-1.5 py-0.5 text-[0.66rem] transition-colors',
                value === mode
                  ? 'bg-(--ui-accent) text-(--ui-accent-foreground, white)'
                  : 'text-(--ui-text-secondary) hover:bg-(--ui-surface-2)'
              )}
              key={value}
              onClick={() => onModeChange(value)}
              type="button"
            >
              {t.calendar.modes[value]}
            </button>
          ))}
        </div>
        <ToolbarButton label={t.timetable.entry} onClick={onImport}>
          <Codicon name="cloud-upload" size="0.9rem" />
        </ToolbarButton>
        <ToolbarButton label={t.newEvent} onClick={onCreate}>
          <Codicon name="add" size="0.9rem" />
        </ToolbarButton>
      </div>

      {importMeta ? (
        <p className="truncate px-3 pb-1 text-[0.6rem] text-(--ui-text-tertiary)">{importMeta}</p>
      ) : null}

      {mode === 'year' ? (
        <div className="min-h-0 flex-1 overflow-y-auto">
          <YearView anchor={anchor} days={days} locale={locale} onOpenDay={openDay} />
        </div>
      ) : mode === 'month' ? (
        <MonthView
          anchor={anchor}
          days={days}
          locale={locale}
          onOpenDay={openDay}
          onSelectEvent={onSelectEvent}
          selectedId={selectedId}
        />
      ) : (
        <>
          {mode === 'day' ? (
            <MiniWeekRow anchor={anchor} days={days} onOpenDay={openDay} today={today} />
          ) : null}
          <TimeAxis
            anchor={anchor}
            columns={columns}
            days={days}
            mode={mode}
            now={now}
            onOpenDay={openDay}
            onSelectEvent={onSelectEvent}
            t={t}
          />
        </>
      )}

      {anchor === today && (days.get(anchor) ?? []).length === 0 && mode === 'day' ? (
        <p className="px-3 pb-2 text-[0.68rem] text-(--ui-text-tertiary)">{t.emptyDesc}</p>
      ) : null}
    </div>
  )
}
