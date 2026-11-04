import { useStore } from '@nanostores/react'
import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'

import { Dialog, DialogContent, DialogTitle } from '@/components/ui/dialog'
import { useI18n } from '@/i18n'
import { closeDiaryCapture, $diaryCaptureOpen } from '@/store/diary-capture'

import { captureDiaryLine, todayISO } from './api'

/**
 * 路过记一句：一行输入，回车即存到当天那篇末尾，窗口自己收掉。
 * 它故意不做富文本、不做日期导航——那是「日记」那一页的活；这里只解决
 * "脑子里刚冒出来一句，别让它跑了"。
 */
export function DiaryCaptureOverlay() {
  const { t } = useI18n()
  const d = t.diary
  const open = useStore($diaryCaptureOpen)
  const queryClient = useQueryClient()
  const [value, setValue] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const inputRef = useRef<HTMLInputElement | null>(null)

  useEffect(() => {
    if (open) {
      setValue('')
      setError('')
      requestAnimationFrame(() => inputRef.current?.focus())
    }
  }, [open])

  const submit = async () => {
    const line = value.trim()
    if (!line || busy) return
    setBusy(true)
    setError('')
    try {
      await captureDiaryLine(todayISO(), line)
      closeDiaryCapture()
      void queryClient.invalidateQueries({ queryKey: ['surface-diary'] })
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog onOpenChange={next => (next ? undefined : closeDiaryCapture())} open={open}>
      <DialogContent className="w-[min(34rem,90vw)] p-0">
        <DialogTitle className="px-4 pt-3 text-[0.7rem] font-normal text-muted-foreground">{d.captureTitle}</DialogTitle>
        <div className="px-4 pb-3 pt-1">
          <input
            className="w-full bg-transparent text-[0.95rem] leading-7 outline-none placeholder:text-muted-foreground/50"
            onChange={event => setValue(event.target.value)}
            onKeyDown={event => {
              if (event.key === 'Enter') {
                event.preventDefault()
                void submit()
              } else if (event.key === 'Escape') {
                event.preventDefault()
                closeDiaryCapture()
              }
            }}
            placeholder={d.capturePlaceholder}
            ref={inputRef}
            value={value}
          />
          <div className="mt-2 flex items-center gap-2 text-[0.62rem] text-muted-foreground/60">
            <span className="min-w-0 flex-1 truncate" title={error}>
              {error || d.captureHint}
            </span>
            <span className="shrink-0">{d.captureEnter}</span>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
