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
import { useI18n } from '@/i18n'
import { selectDesktopPaths } from '@/lib/desktop-fs'
import { getAgenda } from '@/plobi'
import {
  $agendaError,
  $agendaEvents,
  $agendaLoading,
  $agendaPendingCount,
  $agendaSelected,
  setAgendaError,
  setAgendaEvents,
  setAgendaLoading,
  setAgendaSelectedId
} from '@/store/agenda'
import { notify, notifyError } from '@/store/notifications'

import { Panel, PanelBody, PanelEmpty, PanelHeader } from '../overlays/panel'

import { saveAgendaEditorValues, useAgendaEventActions } from './actions'
import { dayKeyOf as calendarDayKey, isoBounds, rangeFor } from './calendar/calendar-model'
import type { CalendarMode } from './calendar/calendar-model'
import { AgendaCalendar } from './calendar/calendar-view'
import { AgendaDetail, AgendaEditorDialog, type AgendaEditorValues, type EditorState } from './detail'
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

  // 动作全部来自 `./actions`（右栏点开详情后用的是同一个 hook，用户 10-31）。
  const actions = useAgendaEventActions()

  async function handleEditorSave(values: AgendaEditorValues) {
    await saveAgendaEditorValues(values, editor.mode === 'edit' ? editor.event : undefined)
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
                attachmentsBusy={actions.attachmentBusy === selected.id}
                busy={actions.busyId === selected.id}
                event={selected}
                onAddAttachment={() => void actions.addAttachments(selected)}
                onConfirm={() => void actions.confirm(selected)}
                onDelete={() => void actions.remove(selected)}
                onDismiss={() => void actions.dismiss(selected)}
                onEdit={() => setEditor({ event: selected, mode: 'edit' })}
                onRemoveAttachment={attachment => void actions.removeAttachment(selected, attachment)}
                onToggleDetail={index => void actions.toggleDetail(selected, index)}
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
