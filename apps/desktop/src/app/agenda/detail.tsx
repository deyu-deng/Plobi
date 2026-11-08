/**
 * 一条日程的详情 + 编辑弹窗（用户 10-31 抽出来）。
 *
 * 原来这两块住在 `app/agenda/index.tsx` 里，只有全屏看板能用。用户 10-31 要
 * 「点右栏的块就地变成这条日程的详情页」，于是把它们搬到这里导出，两处共用
 * 同一份渲染——**不是**再写一套只读弹层。同一批字段只允许有一处渲染，否则两边
 * 迟早各说各话。
 *
 * 这里只负责画：动作（确认/驳回/删除/附件/勾清单）由 `useAgendaEventActions`
 * 提供，编辑态由调用方持有（看板和右栏各有一份，但用的是同一个 `AgendaEditorDialog`）。
 */

import { useEffect, useState } from 'react'
import type * as React from 'react'

import { Button } from '@/components/ui/button'
import { Codicon } from '@/components/ui/codicon'
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
import type { AgendaAttachment, AgendaDetailItem, AgendaEvent, AgendaKind } from '@/types/plobi'

import {
  PanelAction,
  PanelBlock,
  PanelDetail,
  PanelMeta,
  PanelPill,
  type PanelPillTone,
  PanelSectionLabel
} from '../overlays/panel'

export const KINDS: readonly AgendaKind[] = ['meeting', 'ddl', 'class', 'task']

const STATUS_TONE: Record<string, PanelPillTone> = {
  cancelled: 'muted',
  confirmed: 'good',
  pending: 'warn'
}

/** 用户 10-22：少一点文字、多用矢量图标——kind 用手势认得出的 glyph 打头。 */
export interface EditorState {
  event?: AgendaEvent
  mode: 'closed' | 'create' | 'edit'
}

/** Backend speaks naive local ISO strings; keep the desktop on the same shape. */
export function toLocalIso(date: Date): string {
  const pad = (value: number) => String(value).padStart(2, '0')

  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}:00`
}

export function toInputValue(iso: string): string {
  return iso.length >= 16 ? iso.slice(0, 16) : iso
}

export function fromInputValue(value: string): string {
  return value.length === 16 ? `${value}:00` : value
}

/** 附件路径 → `file://` URL；主进程的 openExternal 只认带 scheme 的地址。 */
export function fileUrl(target: string): string {
  const normalized = target.replace(/\\/g, '/')

  return normalized.startsWith('/')
    ? `file://${encodeURI(normalized)}`
    : `file:///${encodeURI(normalized)}`
}

export function newDetailId(): string {
  return `d_${Math.random().toString(36).slice(2, 10)}`
}

export function fileSizeLabel(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) {
    return ''
  }

  if (bytes < 1024) {
    return `${bytes} B`
  }

  if (bytes < 1024 * 1024) {
    return `${(bytes / 1024).toFixed(1)} KB`
  }

  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

/** Who said it: `evidence.sender` (+ conversation name when chatlog has one). */
function senderLabel(event: AgendaEvent, a: Translations['agenda']): string {
  const sender = event.evidence?.sender?.trim() || a.unknown
  const talkerName = event.evidence?.talker_name?.trim()

  return talkerName ? `${sender} · ${talkerName}` : sender
}

export function AgendaDetail({
  a,
  attachmentsBusy,
  busy,
  event,
  onAddAttachment,
  onConfirm,
  onDelete,
  onDismiss,
  onEdit,
  onOpenAttachment,
  onRemoveAttachment,
  onToggleDetail
}: {
  a: Translations['agenda']
  attachmentsBusy: boolean
  busy: boolean
  event: AgendaEvent
  onAddAttachment: () => void
  onConfirm: () => void
  onDelete: () => void
  onDismiss: () => void
  onEdit: () => void
  /**
   * 点附件。右栏传「在当前右栏新开一个页签看内容」（用户 10-31）；
   * 看板不传，退回系统默认打开（老行为）。
   */
  onOpenAttachment?: (attachment: AgendaAttachment) => void
  onRemoveAttachment: (attachment: AgendaAttachment) => void
  onToggleDetail: (index: number) => void
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

      {event.details?.length ? (
        <section className="space-y-1.5">
          <PanelSectionLabel>{a.detailsLabel}</PanelSectionLabel>
          <ul className="space-y-1">
            {event.details.map((item, index) => (
              <li className="flex items-start gap-2 text-[0.8rem]" key={item.id}>
                <input
                  aria-label={item.text}
                  checked={item.done}
                  className="mt-0.5 size-3.5 shrink-0 accent-(--ui-accent)"
                  onChange={() => onToggleDetail(index)}
                  type="checkbox"
                />
                <span className={item.done ? 'text-muted-foreground line-through' : 'text-foreground'}>
                  {item.text}
                </span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      <section className="space-y-1.5">
        <div className="flex items-center justify-between gap-2">
          <PanelSectionLabel>{a.attachmentsLabel}</PanelSectionLabel>
          <button
            aria-label={a.addAttachment}
            className="flex size-6 items-center justify-center rounded text-(--ui-text-tertiary) hover:bg-(--ui-control-hover-background) hover:text-foreground disabled:opacity-50"
            disabled={attachmentsBusy}
            onClick={onAddAttachment}
            title={a.addAttachment}
            type="button"
          >
            <Codicon name="add" size="0.8rem" />
          </button>
        </div>
        {(event.attachments ?? []).length === 0 ? (
          <p className="text-[0.7rem] text-(--ui-text-tertiary)">{a.noAttachments}</p>
        ) : (
          <ul className="space-y-1">
            {(event.attachments ?? []).map(attachment => (
              <li className="flex items-center gap-1.5 rounded-md border border-(--ui-stroke-tertiary) px-1.5 py-1" key={attachment.id}>
                <Codicon className="shrink-0 text-(--ui-text-tertiary)" name="file" size="0.8rem" />
                <button
                  className="min-w-0 flex-1 truncate text-left text-[0.75rem] text-foreground hover:underline"
                  onClick={() => {
                    if (onOpenAttachment) {
                      onOpenAttachment(attachment)

                      return
                    }

                    void window.plobiDesktop?.openExternal?.(fileUrl(attachment.path))
                  }}
                  title={`${a.openAttachment}: ${attachment.path}`}
                  type="button"
                >
                  {attachment.name}
                </button>
                <span className="shrink-0 text-[0.6rem] tabular-nums text-(--ui-text-tertiary)">
                  {fileSizeLabel(attachment.size)}
                </span>
                <button
                  aria-label={a.removeAttachment}
                  className="flex size-5 shrink-0 items-center justify-center rounded text-(--ui-text-tertiary) hover:bg-(--ui-control-hover-background) hover:text-destructive disabled:opacity-50"
                  disabled={attachmentsBusy}
                  onClick={() => onRemoveAttachment(attachment)}
                  title={a.removeAttachment}
                  type="button"
                >
                  <Codicon name="trash" size="0.75rem" />
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

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

export interface AgendaEditorValues {
  details: AgendaDetailItem[]
  endAt: null | string
  kind: AgendaKind
  location: null | string
  notes: null | string
  startAt: string
  title: string
}

export function AgendaEditorDialog({
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
  const [details, setDetails] = useState<AgendaDetailItem[]>([])
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
    setDetails((initial?.details ?? []).map(row => ({ ...row })))
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
        details: details
          .map(row => ({ ...row, text: row.text.trim() }))
          .filter(row => row.text.length > 0),
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
          <div className="space-y-1.5">
            <div className="flex items-center justify-between gap-2">
              <span className="text-[0.72rem] text-(--ui-text-secondary)">{a.detailsLabel}</span>
              <button
                className="flex items-center gap-1 rounded px-1 py-0.5 text-[0.66rem] text-(--ui-text-tertiary) hover:bg-(--ui-control-hover-background) hover:text-foreground"
                onClick={() => setDetails(rows => [...rows, { done: false, id: newDetailId(), text: '' }])}
                type="button"
              >
                <Codicon name="add" size="0.75rem" />
                {a.addDetail}
              </button>
            </div>
            {details.map((row, index) => (
              <div className="flex items-center gap-1" key={row.id}>
                <input
                  aria-label={a.detailPlaceholder}
                  className="min-w-0 flex-1 rounded border border-(--ui-stroke-tertiary) bg-transparent px-2 py-1 text-[0.78rem] outline-none placeholder:text-(--ui-text-tertiary)"
                  onChange={changeEvent => {
                    const text = changeEvent.target.value

                    setDetails(rows =>
                      rows.map((item, position) => (position === index ? { ...item, text } : item))
                    )
                  }}
                  placeholder={a.detailPlaceholder}
                  value={row.text}
                />
                {row.done ? (
                  <Codicon className="shrink-0 text-emerald-500" name="check" size="0.8rem" />
                ) : null}
                <button
                  aria-label={t.common.delete}
                  className="flex size-6 shrink-0 items-center justify-center rounded text-(--ui-text-tertiary) hover:bg-(--ui-control-hover-background) hover:text-destructive"
                  onClick={() => setDetails(rows => rows.filter((_, position) => position !== index))}
                  type="button"
                >
                  <Codicon name="trash" size="0.75rem" />
                </button>
              </div>
            ))}
          </div>
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
