import { useStore } from '@nanostores/react'
import type * as React from 'react'
import { useCallback, useEffect, useMemo, useState } from 'react'

import { PageLoader } from '@/components/page-loader'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { type Translations, useI18n } from '@/i18n'
import { selectDesktopPaths } from '@/lib/desktop-fs'
import {
  confirmAgendaEvent,
  createAgendaEvent,
  deleteAgendaEvent,
  dismissAgendaEvent,
  getAgenda,
  updateAgendaEvent
} from '@/plobi'
import {
  $agendaError,
  $agendaEvents,
  $agendaLoading,
  $agendaPendingCount,
  $agendaSelected,
  removeAgendaEvent,
  setAgendaError,
  setAgendaEvents,
  setAgendaLoading,
  setAgendaSelectedId,
  upsertAgendaEvent
} from '@/store/agenda'
import { notify, notifyError } from '@/store/notifications'
import type { AgendaEvent, AgendaKind } from '@/types/plobi'

import {
  Panel,
  PanelAction,
  PanelBlock,
  PanelBody,
  PanelDetail,
  PanelEmpty,
  PanelHeader,
  PanelMeta,
  PanelPill,
  type PanelPillTone,
  PanelSectionLabel
} from '../overlays/panel'

import { dayKeyOf as calendarDayKey, isoBounds, rangeFor } from './calendar/calendar-model'
import type { CalendarMode } from './calendar/calendar-model'
import { AgendaCalendar } from './calendar/calendar-view'
import {
  getTimetable,
  importTimetable,
  previewTimetable,
  type TimetablePreview,
  type TimetableSampleEvent,
  type TimetableStatus
} from './timetable/api'

// Board refresh cadence. The spec allows up to 10s staleness (ADR-0008), and
// polling keeps us off SSE/WebSocket plumbing for a single-user desktop.
const POLL_INTERVAL_MS = 8000

const KINDS: readonly AgendaKind[] = ['meeting', 'ddl', 'class', 'task']

const STATUS_TONE: Record<string, PanelPillTone> = {
  cancelled: 'muted',
  confirmed: 'good',
  pending: 'warn'
}

/** 用户 10-22：少一点文字、多用矢量图标——kind 用手势认得出的 glyph 打头。 */
interface EditorState {
  event?: AgendaEvent
  mode: 'closed' | 'create' | 'edit'
}

/** Backend speaks naive local ISO strings; keep the desktop on the same shape. */
function toLocalIso(date: Date): string {
  const pad = (value: number) => String(value).padStart(2, '0')

  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}:00`
}

function toInputValue(iso: string): string {
  return iso.length >= 16 ? iso.slice(0, 16) : iso
}

function fromInputValue(value: string): string {
  return value.length === 16 ? `${value}:00` : value
}

/** Who said it: `evidence.sender` (+ conversation name when chatlog has one). */
function senderLabel(event: AgendaEvent, a: Translations['agenda']): string {
  const sender = event.evidence?.sender?.trim() || a.unknown
  const talkerName = event.evidence?.talker_name?.trim()

  return talkerName ? `${sender} · ${talkerName}` : sender
}

function timetableClock(iso: string): string {
  return iso && iso.length >= 16 ? iso.slice(11, 16) : (iso ?? '')
}

/**
 * WP-ICS-BOARD: one preview line — `HH:MM–HH:MM 标题 · 地点`. The backend hands
 * us the fields; we print them and never fill in a missing end time ourselves.
 */
function timetableSampleLine(sample: TimetableSampleEvent): string {
  const start = timetableClock(sample.start_at)
  const end = sample.end_at ? timetableClock(sample.end_at) : ''
  const when = start ? (end ? `${start}–${end}` : start) : ''
  const head = [when, sample.title].filter(Boolean).join(' ')
  const location = sample.location?.trim()

  return location ? `${head} · ${location}` : head
}

function isDeleted(value: unknown): value is { deleted: true; id: string } {
  return typeof value === 'object' && value !== null && 'deleted' in value
}

interface AgendaViewProps extends React.ComponentProps<'section'> {
  /** Test seam: the calendar anchors on this instant instead of the wall clock. */
  now?: Date
  onClose: () => void
}

export function AgendaView({ now = new Date(), onClose }: AgendaViewProps) {
  const { t } = useI18n()
  const a = t.agenda
  const events = useStore($agendaEvents)
  const loading = useStore($agendaLoading)
  const error = useStore($agendaError)
  const selected = useStore($agendaSelected)
  const pendingCount = useStore($agendaPendingCount)

  const [editor, setEditor] = useState<EditorState>({ mode: 'closed' })
  // 用户 10-23：年 / 月 / 周 / 日四态，仿鸿蒙日历；默认停在「日」，与右栏的
  // 「一次只显示一天」同一口径。
  const [mode, setMode] = useState<CalendarMode>('day')
  const [anchor, setAnchor] = useState(() => calendarDayKey(now))
  const range = useMemo(() => rangeFor(mode, anchor), [anchor, mode])
  const rangeKey = `${range.from}|${range.to}`
  const [busyId, setBusyId] = useState<null | string>(null)
  // WP-ICS-BOARD: `path` → preview draft; `preview: null` means "still reading"
  // (the dialog shows PageLoader). Nothing is written until the user confirms.
  const [timetable, setTimetable] = useState<null | { path: string; preview: null | TimetablePreview }>(null)
  const [timetableBusy, setTimetableBusy] = useState(false)
  const [timetableStatus, setTimetableStatus] = useState<null | TimetableStatus>(null)

  const refresh = useCallback(async () => {
    try {
      const [from, to] = isoBounds(range)
      const rows = await getAgenda(from, to)

      setAgendaEvents(rows)
      setAgendaError(null)
    } catch (cause) {
      setAgendaError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setAgendaLoading(false)
    }
    // range 由 mode + anchor 推出来；两者变了才换窗口。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rangeKey])

  useEffect(() => {
    void refresh()

    const intervalId = window.setInterval(() => {
      if (document.visibilityState === 'visible') {
        void refresh()
      }
    }, POLL_INTERVAL_MS)

    return () => window.clearInterval(intervalId)
  }, [refresh])

  // A7 (WP-A7-EMPTY): the collection entry used to prefetch here; 用户 10-22 拍「信源只进信息池、
  // Agent 自由调用、不直接落日程库」，日程面板不再提供采集入口（也没有要预取的采集状态）。

  // WP-ICS-BOARD: the 「导入日程文件」 row's meta only appears once something was
  // imported — never a placeholder.
  useEffect(() => {
    void getTimetable().then(setTimetableStatus)
  }, [])

  const handlePickTimetable = useCallback(async () => {
    try {
      const paths = await selectDesktopPaths({
        filters: [{ name: a.timetable.filterName, extensions: ['ics', 'ical'] }],
        multiple: false
      })

      const path = paths[0]

      // Cancelled picker → nothing happens at all (no preview, no write).
      if (!path) {
        return
      }

      setTimetable({ path, preview: null })
      setTimetableBusy(true)

      try {
        const preview = await previewTimetable(path)

        setTimetable({ path, preview })
      } catch (cause) {
        // Preview is read-only: report and close, the agenda is untouched.
        setTimetable(null)
        notifyError(cause, a.timetable.loadFailed)
      } finally {
        setTimetableBusy(false)
      }
    } catch (cause) {
      notifyError(cause, a.timetable.loadFailed)
    }
  }, [a])

  const handleConfirmTimetable = useCallback(async () => {
    if (!timetable?.preview) {
      return
    }

    setTimetableBusy(true)

    try {
      const result = await importTimetable(timetable.path)

      notify({ message: a.timetable.imported(result.count) })
      setTimetable(null)
      // Pull once immediately — the 8s board poll is not the point where the
      // user should first see their timetable.
      await refresh()
      setTimetableStatus(await getTimetable())
    } catch (cause) {
      notifyError(cause, a.timetable.importFailed)
    } finally {
      setTimetableBusy(false)
    }
  }, [a, refresh, timetable])

  // 「已导入过」的小字：只在 GET 真有数据时出现（进行中显示读取状态）。
  const timetableMeta = timetableBusy
    ? a.timetable.reading
    : timetableStatus
      ? a.timetable.importedMeta(timetableStatus.calendar_name, timetableStatus.event_count)
      : undefined

  async function handleConfirm(event: AgendaEvent) {
    setBusyId(event.id)

    try {
      upsertAgendaEvent(await confirmAgendaEvent(event.id))
      notify({ message: a.confirmed })
    } catch (cause) {
      notifyError(cause, a.actionFailed)
    } finally {
      setBusyId(null)
    }
  }

  async function handleDismiss(event: AgendaEvent) {
    setBusyId(event.id)

    try {
      const result = await dismissAgendaEvent(event.id)

      if (isDeleted(result)) {
        removeAgendaEvent(event.id)
      } else {
        upsertAgendaEvent(result)
      }

      notify({ message: a.dismissed })
    } catch (cause) {
      notifyError(cause, a.actionFailed)
    } finally {
      setBusyId(null)
    }
  }

  async function handleDelete(event: AgendaEvent) {
    setBusyId(event.id)

    try {
      await deleteAgendaEvent(event.id)
      removeAgendaEvent(event.id)
      notify({ message: a.deleted })
    } catch (cause) {
      notifyError(cause, a.actionFailed)
    } finally {
      setBusyId(null)
    }
  }

  async function handleEditorSave(values: {
    endAt: null | string
    kind: AgendaKind
    location: null | string
    notes: null | string
    startAt: string
    title: string
  }) {
    if (editor.mode === 'edit' && editor.event) {
      // The editor intentionally keeps `null` distinct from `undefined`: a
      // missing field lets the backend preserve its current value, an
      // explicit null wipes the field back to nothing.
      const patch: { end_at: null | string; kind: AgendaKind; location: null | string; notes: null | string; start_at: string; title: string } = {
        end_at: values.endAt,
        kind: values.kind,
        location: values.location,
        notes: values.notes,
        start_at: values.startAt,
        title: values.title
      }

      upsertAgendaEvent(await updateAgendaEvent(editor.event.id, patch))
    } else {
      const created = await createAgendaEvent({
        end_at: values.endAt ?? undefined,
        kind: values.kind,
        location: values.location,
        notes: values.notes,
        start_at: values.startAt,
        title: values.title
      })

      upsertAgendaEvent(created)
      setAgendaSelectedId(created.id)
    }

    setEditor({ mode: 'closed' })
  }

  return (
    <Panel closeLabel={a.close} onClose={onClose}>
      {loading && events.length === 0 ? (
        <PageLoader label={a.loading} />
      ) : error && events.length === 0 ? (
        <PanelEmpty description={error} icon="warning" title={a.loadFailed} />
      ) : (
        <>
          <PanelHeader
            subtitle={pendingCount > 0 ? a.pendingCount(pendingCount) : a.count(events.length)}
            title={a.title}
          />
          {/*
            * 日程＝计划（用户 10-22）：这里不再挂「明天的计划」那一层。计划整层退役，
            * 夜间排程改为直接写 events，看板只认事件行。
            * 用户 10-23：日 / 周 / 月 / 年四态，仿鸿蒙日历；点日进日视图，点事件看详情。
            */}
          <PanelBody>
            <div className="flex min-h-0 min-w-0 flex-1 flex-col">
              <AgendaCalendar
                anchor={anchor}
                events={events}
                importMeta={timetableMeta}
                mode={mode}
                now={now}
                onAnchorChange={setAnchor}
                onCreate={() => setEditor({ mode: 'create' })}
                onImport={() => void handlePickTimetable()}
                onModeChange={setMode}
                onSelectEvent={id => setAgendaSelectedId(id)}
                selectedId={selected?.id ?? null}
                t={a}
              />
            </div>

            {selected ? (
              <AgendaDetail
                a={a}
                busy={busyId === selected.id}
                event={selected}
                onConfirm={() => void handleConfirm(selected)}
                onDelete={() => void handleDelete(selected)}
                onDismiss={() => void handleDismiss(selected)}
                onEdit={() => setEditor({ event: selected, mode: 'edit' })}
              />
            ) : events.length === 0 ? (
              <PanelEmpty
                action={
                  <Button onClick={() => setEditor({ mode: 'create' })} size="sm">
                    {a.newEvent}
                  </Button>
                }
                description={a.emptyDesc}
                icon="calendar"
                title={a.emptyTitle}
              />
            ) : (
              <PanelEmpty description={a.emptyDesc} icon="calendar" />
            )}
          </PanelBody>
        </>
      )}

      <AgendaEditorDialog
        a={a}
        editor={editor}
        onClose={() => setEditor({ mode: 'closed' })}
        onSave={handleEditorSave}
      />

      {timetable ? (
        <Dialog
          onOpenChange={value => {
            if (!value && !timetableBusy) {
              setTimetable(null)
            }
          }}
          open
        >
          <DialogContent className="max-w-lg">
            <DialogHeader>
              <DialogTitle>{a.timetable.entry}</DialogTitle>
            </DialogHeader>

            {timetable.preview ? (
              <div className="space-y-2 text-[0.8rem]">
                <div className="font-medium text-foreground">{timetable.preview.calendar_name}</div>
                <div className="text-muted-foreground">
                  {a.timetable.courseCount(timetable.preview.count, timetable.preview.courses)}
                </div>
                <div className="text-muted-foreground">{a.timetable.span(timetable.preview.first, timetable.preview.last)}</div>
                {timetable.preview.sample.length > 0 ? (
                  <div className="space-y-0.5 pt-1" data-testid="timetable-sample">
                    {timetable.preview.sample.slice(0, 5).map((sample, index) => (
                      <div className="truncate text-muted-foreground/80" key={`${sample.title}-${index}`}>
                        {timetableSampleLine(sample)}
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="text-muted-foreground">{a.timetable.empty}</div>
                )}
              </div>
            ) : (
              <PageLoader label={a.timetable.reading} />
            )}

            <DialogFooter>
              <Button disabled={timetableBusy} onClick={() => setTimetable(null)} type="button" variant="outline">
                {t.common.cancel}
              </Button>
              <Button
                disabled={timetableBusy || !timetable.preview}
                onClick={() => void handleConfirmTimetable()}
                type="button"
              >
                {a.timetable.import}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      ) : null}
    </Panel>
  )
}

function AgendaDetail({
  a,
  busy,
  event,
  onConfirm,
  onDelete,
  onDismiss,
  onEdit
}: {
  a: Translations['agenda']
  busy: boolean
  event: AgendaEvent
  onConfirm: () => void
  onDelete: () => void
  onDismiss: () => void
  onEdit: () => void
}) {
  const { t } = useI18n()
  const isPending = event.status === 'pending'
  const previous = event.prev_value

  return (
    <PanelDetail>
      <header className="space-y-3">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="flex min-w-0 flex-wrap items-center gap-2">
            <h3 className="text-[0.95rem] font-semibold tracking-tight text-foreground">{event.title}</h3>
            <PanelPill tone={STATUS_TONE[event.status] ?? 'muted'}>{a.statuses[event.status]}</PanelPill>
          </div>
          <div className="flex shrink-0 items-center gap-0.5">
            {isPending ? (
              <>
                <PanelAction disabled={busy} icon="check" onClick={onConfirm}>
                  {a.confirm}
                </PanelAction>
                <PanelAction disabled={busy} icon="discard" onClick={onDismiss}>
                  {a.dismiss}
                </PanelAction>
              </>
            ) : (
              <PanelAction disabled={busy} icon="edit" onClick={onEdit}>
                {a.edit}
              </PanelAction>
            )}
            <PanelAction disabled={busy} icon="trash" onClick={onDelete}>
              {t.common.delete}
            </PanelAction>
          </div>
        </div>

        <PanelMeta
          rows={[
            { label: a.startLabel, value: event.start_at.replace('T', ' ') },
            { label: a.endLabel, value: event.end_at ? event.end_at.replace('T', ' ') : a.noEnd },
            { label: a.kindLabel, value: a.kinds[event.kind] },
            { label: a.sourceLabel, value: a.sources[event.source] },
            { label: a.senderLabel, value: senderLabel(event, a) },
            ...(event.location?.trim() ? [{ label: a.locationLabel, value: event.location.trim() }] : [])
          ]}
        />
      </header>

      {event.notes?.trim() ? (
        <section className="space-y-1.5">
          <PanelSectionLabel>{a.notesLabel}</PanelSectionLabel>
          <PanelBlock>{event.notes.trim()}</PanelBlock>
        </section>
      ) : null}

      {isPending && previous ? (
        <section className="space-y-1.5">
          <PanelSectionLabel>{a.changeLabel}</PanelSectionLabel>
          <PanelMeta
            rows={[
              ...(previous.title && previous.title !== event.title
                ? [{ label: a.titleField, value: `${previous.title} → ${event.title}` }]
                : []),
              ...(previous.start_at && previous.start_at !== event.start_at
                ? [{ label: a.startLabel, value: `${previous.start_at.replace('T', ' ')} → ${event.start_at.replace('T', ' ')}` }]
                : []),
              ...(previous.end_at && previous.end_at !== event.end_at
                ? [{ label: a.endLabel, value: `${previous.end_at.replace('T', ' ')} → ${event.end_at ? event.end_at.replace('T', ' ') : a.noEnd}` }]
                : [])
            ]}
          />
        </section>
      ) : null}

      {event.evidence?.snippet ? (
        <section className="space-y-1.5">
          <PanelSectionLabel>{a.evidenceLabel}</PanelSectionLabel>
          <PanelBlock>{event.evidence.snippet}</PanelBlock>
        </section>
      ) : null}
    </PanelDetail>
  )
}

interface AgendaEditorValues {
  endAt: null | string
  kind: AgendaKind
  location: null | string
  notes: null | string
  startAt: string
  title: string
}

function AgendaEditorDialog({
  a,
  editor,
  onClose,
  onSave
}: {
  a: Translations['agenda']
  editor: EditorState
  onClose: () => void
  onSave: (values: AgendaEditorValues) => Promise<void>
}) {
  const { t } = useI18n()
  const open = editor.mode !== 'closed'
  const initial = editor.mode === 'edit' ? editor.event : undefined

  const [title, setTitle] = useState('')
  const [startAt, setStartAt] = useState('')
  const [endAt, setEndAt] = useState('')
  const [location, setLocation] = useState('')
  const [notes, setNotes] = useState('')
  const [kind, setKind] = useState<AgendaKind>('task')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<null | string>(null)

  useEffect(() => {
    if (!open) {
      return
    }

    const fallback = new Date()

    fallback.setMinutes(0, 0, 0)
    fallback.setHours(fallback.getHours() + 1)

    setTitle(initial?.title ?? '')
    setStartAt(toInputValue(initial?.start_at ?? toLocalIso(fallback)))
    // End is intentionally empty when the existing record is open-ended — we
    // must NOT fabricate a start+1h default (that's exactly what the ruling
    // forbids, and the test pins it).
    setEndAt(initial?.end_at ? toInputValue(initial.end_at) : '')
    setLocation(initial?.location?.trim() ?? '')
    setNotes(initial?.notes?.trim() ?? '')
    setKind(initial?.kind ?? 'task')
    setError(null)
    setSaving(false)
  }, [initial, open])

  async function handleSubmit(submitEvent: React.FormEvent) {
    submitEvent.preventDefault()

    if (!title.trim() || !startAt) {
      setError(a.titleTimeRequired)

      return
    }

    const startIso = fromInputValue(startAt)
    let endIso: null | string = null

    if (endAt) {
      const candidate = fromInputValue(endAt)

      if (candidate <= startIso) {
        setError(a.endBeforeStart)

        return
      }

      endIso = candidate
    }

    setSaving(true)
    setError(null)

    try {
      await onSave({
        endAt: endIso,
        kind,
        location: location.trim() || null,
        notes: notes.trim() || null,
        startAt: startIso,
        title: title.trim()
      })
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
      setSaving(false)
    }
  }

  return (
    <Dialog onOpenChange={value => !value && !saving && onClose()} open={open}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>{editor.mode === 'edit' ? a.editTitle : a.createTitle}</DialogTitle>
        </DialogHeader>

        <form className="space-y-3" onSubmit={handleSubmit}>
          <Input
            aria-label={a.titleField}
            onChange={changeEvent => setTitle(changeEvent.target.value)}
            placeholder={a.titlePlaceholder}
            value={title}
          />
          <Input
            aria-label={a.startLabel}
            onChange={changeEvent => setStartAt(changeEvent.target.value)}
            type="datetime-local"
            value={startAt}
          />
          <Input
            aria-label={a.endLabel}
            onChange={changeEvent => setEndAt(changeEvent.target.value)}
            placeholder={a.noEnd}
            type="datetime-local"
            value={endAt}
          />
          <Input
            aria-label={a.locationLabel}
            onChange={changeEvent => setLocation(changeEvent.target.value)}
            placeholder={a.locationPlaceholder}
            value={location}
          />
          <textarea
            aria-label={a.notesLabel}
            className="min-h-16 w-full resize-y rounded-md border border-(--ui-stroke-tertiary) bg-transparent px-2 py-1.5 text-[0.8rem] outline-none placeholder:text-(--ui-text-tertiary)"
            onChange={changeEvent => setNotes(changeEvent.target.value)}
            placeholder={a.notesPlaceholder}
            value={notes}
          />
          <Select onValueChange={value => setKind(value as AgendaKind)} value={kind}>
            <SelectTrigger aria-label={a.kindLabel}>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {KINDS.map(value => (
                <SelectItem key={value} value={value}>
                  {a.kinds[value]}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>

          {error ? <p className="text-xs text-destructive">{error}</p> : null}

          <DialogFooter>
            <Button disabled={saving} onClick={onClose} type="button" variant="outline">
              {t.common.cancel}
            </Button>
            <Button disabled={saving} type="submit">
              {t.common.save}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
