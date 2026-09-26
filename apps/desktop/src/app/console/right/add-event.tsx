/**
 * Manual agenda entry (U3, spec §3.3). Reuses the live backend
 * `createAgendaEvent` (`@/plobi`) and writes the answer back into the shared
 * `agenda` store via `upsertAgendaEvent` — same path the board uses, so the
 * new row appears in both the rail and the full board.
 */

import { useState } from 'react'

import { Button } from '@/components/ui/button'
import { createAgendaEvent } from '@/plobi'
import { useI18n } from '@/i18n'
import { upsertAgendaEvent } from '@/store/agenda'

function localDatetimeValue(date: Date): string {
  const pad = (value: number) => String(value).padStart(2, '0')

  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`
}

export function AddEventForm({ onClose }: { onClose: () => void }) {
  const { t } = useI18n()
  const c = t.console.timeline
  const [title, setTitle] = useState('')
  const [start, setStart] = useState(localDatetimeValue(new Date()))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const submit = async () => {
    const trimmed = title.trim()

    if (!trimmed || busy) {
      return
    }

    setBusy(true)
    setError(null)

    try {
      const created = await createAgendaEvent({ start_at: start, title: trimmed })

      upsertAgendaEvent(created)
      onClose()
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : String(failure))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex flex-col gap-2 border-t border-(--ui-border) px-2 py-2">
      <label className="flex flex-col gap-0.5 text-[0.62rem] text-(--ui-text-tertiary)">
        {c.addTitle}
        <input
          autoFocus
          className="rounded-md border border-(--ui-border) bg-(--ui-bg-elevated) px-2 py-1 text-[0.78rem] text-foreground outline-none focus:border-(--ui-accent)"
          onChange={event => setTitle(event.target.value)}
          placeholder={c.addTitlePlaceholder}
          value={title}
        />
      </label>
      <label className="flex flex-col gap-0.5 text-[0.62rem] text-(--ui-text-tertiary)">
        {c.addStart}
        <input
          className="rounded-md border border-(--ui-border) bg-(--ui-bg-elevated) px-2 py-1 text-[0.78rem] text-foreground outline-none focus:border-(--ui-accent)"
          onChange={event => setStart(event.target.value)}
          type="datetime-local"
          value={start}
        />
      </label>
      {error ? <p className="text-[0.62rem] text-red-500/80">{error}</p> : null}
      <div className="flex justify-end gap-1.5">
        <Button disabled={busy} onClick={onClose} size="xs" variant="ghost">
          {c.addCancel}
        </Button>
        <Button disabled={busy || !title.trim()} onClick={() => void submit()} size="xs">
          {c.addSubmit}
        </Button>
      </div>
    </div>
  )
}
