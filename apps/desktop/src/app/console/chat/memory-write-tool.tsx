import { useState } from 'react'

import { Button } from '@/components/ui/button'
import { useI18n } from '@/i18n'

/**
 * 对话里那张「它想记住这条」的小卡片（用户 2026-10-30：像请求运行命令那样的交互）。
 *
 * 挂法跟 `clarify` / `image_generate` / `agenda_propose` 一样，由 `ChainToolFallback`
 * 按工具名分派；只有**确实进了待确认层**的结果（`{staged:true, pending_id}`）才出卡片，
 * 直接写成功的那些照常走默认工具行。
 *
 * 文案全部复用已有键：按钮用 `mindPending.approve/reject`（"点头/驳回"），
 * 结果用 `console.chat.cardConfirmed/cardDismissed/cardFailed`——这一刀不新增任何字符串，
 * 也就不会在四语那五档上再欠一笔。
 */

interface MemoryWriteProps {
  args?: unknown
  result?: unknown
}

function asRecord(value: unknown): Record<string, unknown> | null {
  if (value && typeof value === 'object') {
    return value as Record<string, unknown>
  }
  if (typeof value === 'string') {
    try {
      const parsed = JSON.parse(value)
      return parsed && typeof parsed === 'object' ? (parsed as Record<string, unknown>) : null
    } catch {
      return null
    }
  }
  return null
}

export function stagedMemoryPendingId(result: unknown): string {
  const payload = asRecord(result)
  if (!payload || payload.staged !== true) {
    return ''
  }
  return typeof payload.pending_id === 'string' ? payload.pending_id : ''
}

function memoryText(args: unknown): string {
  const a = asRecord(args) ?? {}
  const parts = Array.isArray(a.parts) ? a.parts.filter(p => typeof p === 'string') : []
  const content = typeof a.content === 'string' ? a.content : ''
  const oldText = typeof a.old_text === 'string' ? a.old_text : ''
  const joined = (parts.length > 0 ? parts.join(' / ') : content) || oldText
  return joined.trim()
}

export function MemoryWriteTool({ args, result }: MemoryWriteProps) {
  const { t } = useI18n()
  const c = t.console.chat
  const m = t.mindPending
  const pendingId = stagedMemoryPendingId(result)
  const body = memoryText(args)
  const [busy, setBusy] = useState(false)
  const [outcome, setOutcome] = useState<'dismissed' | 'confirmed' | 'error' | null>(null)

  if (!pendingId) {
    return <div className="my-2 text-[0.75rem] text-(--ui-text-tertiary)">{c.cardInvalid}</div>
  }

  if (outcome) {
    return (
      <div className="my-2 border-l-2 border-(--ui-border) pl-3 text-[0.75rem] text-(--ui-text-tertiary)">
        {outcome === 'confirmed' ? c.cardConfirmed : outcome === 'dismissed' ? c.cardDismissed : c.cardFailed}
      </div>
    )
  }

  const act = async (verb: 'approve' | 'reject') => {
    setBusy(true)
    try {
      await window.plobiDesktop.api<{ ok?: boolean }>({
        method: 'POST',
        path: `/api/memory/pending/${encodeURIComponent(pendingId)}/${verb}`
      })
      setOutcome(verb === 'approve' ? 'confirmed' : 'dismissed')
    } catch {
      setOutcome('error')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="my-2 flex flex-col gap-2 border-l-2 border-(--ui-accent) pl-3">
      <div className="text-[0.82rem] whitespace-pre-wrap break-words text-foreground/90">{body}</div>
      <div className="flex gap-2">
        <Button disabled={busy} onClick={() => void act('approve')} size="sm">
          {m.approve}
        </Button>
        <Button disabled={busy} onClick={() => void act('reject')} size="sm" variant="ghost">
          {m.reject}
        </Button>
      </div>
    </div>
  )
}
