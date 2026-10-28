import { useStore } from '@nanostores/react'
import { useEffect, useState } from 'react'

import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle
} from '@/components/ui/dialog'
import { useI18n } from '@/i18n'
import {
  $mindPending,
  $mindPendingOpen,
  actOnMindPending,
  refreshMindPending,
  setMindPendingOpen
} from '@/store/mind-pending'

/**
 * 等你点头的那几页（`WP-MIND-PENDING-VISIBLE`）。
 *
 * 屏幕只写「这是什么」：一条＝要写进脑的哪一格；机制（TTL、信任账、哈希绑根）留在这里之外。
 * 过期那张仍然列出来并标「已过期」——它批不动，但藏着不让人知道更糟。
 */
export function MindPendingOverlay() {
  const { t } = useI18n()
  const m = t.mindPending
  const open = useStore($mindPendingOpen)
  const state = useStore($mindPending)
  const [busy, setBusy] = useState<string | null>(null)
  const [failed, setFailed] = useState<string | null>(null)

  useEffect(() => {
    if (!open) {
      return undefined
    }
    void refreshMindPending()
    const timer = window.setInterval(() => void refreshMindPending(), 30_000)
    return () => window.clearInterval(timer)
  }, [open])

  const act = async (id: string, verb: 'approve' | 'reject') => {
    setBusy(`${id}:${verb}`)
    try {
      const next = await actOnMindPending(id, verb)
      if (verb === 'approve' && next.ok === false) {
        setFailed(next.message || m.failed)
      } else {
        setFailed(null)
      }
    } catch {
      setFailed(m.failed)
    } finally {
      setBusy(null)
    }
  }

  return (
    <Dialog onOpenChange={setMindPendingOpen} open={open}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>{m.title}</DialogTitle>
          <DialogDescription>{state.gate ? m.desc : m.gateOff}</DialogDescription>
        </DialogHeader>

        {failed ? (
          <div className="text-destructive text-sm" role="alert">
            {failed}
          </div>
        ) : null}

        {state.items.length === 0 ? (
          <div className="text-muted-foreground py-6 text-center text-sm">{m.empty}</div>
        ) : (
          <ul className="flex flex-col gap-3">
            {state.items.map(item => (
              <li className="rounded-md border border-border p-3" key={item.id}>
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0 flex-1">
                    {item.paths.length > 0 ? (
                      <div className="truncate text-sm" title={item.paths.join('\n')}>
                        {item.paths[0]}
                        {item.paths.length > 1 ? ` +${item.paths.length - 1}` : ''}
                      </div>
                    ) : (
                      <div className="text-sm">{item.summary || item.id}</div>
                    )}
                    {item.expired ? (
                      <div className="text-muted-foreground text-xs">{m.expired}</div>
                    ) : null}
                  </div>
                  <div className="flex shrink-0 gap-2">
                    <Button
                      disabled={Boolean(busy) || item.expired}
                      onClick={() => void act(item.id, 'approve')}
                      size="sm"
                    >
                      {m.approve}
                    </Button>
                    <Button
                      disabled={Boolean(busy)}
                      onClick={() => void act(item.id, 'reject')}
                      size="sm"
                      variant="ghost"
                    >
                      {m.reject}
                    </Button>
                  </div>
                </div>
              </li>
            ))}
          </ul>
        )}
      </DialogContent>
    </Dialog>
  )
}
