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
import { Check, MessageSquareText, X } from '@/lib/icons'
import {
  $mindPending,
  $mindPendingOpen,
  actOnMindPending,
  commentOnMindPending,
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
  // 四语键归在派的 `WP-TERMS-FOUR-WORDS`，本刀不冒那个前缀、不硬改 i18n：
  // 复用现成的通用词「批注／Note／メモ」（`t.workbench.office.note` 那对键）与 `common.save`。
  // 记作缺口：待四语那一刀落地后换成 `mindPending.comment`。
  const noteWord = t.workbench.office.note
  const open = useStore($mindPendingOpen)
  const state = useStore($mindPending)
  const [busy, setBusy] = useState<string | null>(null)
  const [failed, setFailed] = useState<string | null>(null)
  const [commentFor, setCommentFor] = useState<string | null>(null)
  const [commentText, setCommentText] = useState('')

  // 轮询挂在这个组件上，不挂在那颗角标上：`use-statusbar-items.tsx` 由在派的
  // `WP-WORKBENCH-LAUNCHER` 独占（串行一次一家，我不抢），而这一档是本刀新建的、归本线。
  // 覆盖层平时是关着的，但 store 是共享的 ⇒ 角标数照刷。进闸的纸是 cron 在后台写的，
  // 只在打开时才拉一次＝数永远是 0，那等于把「进人眼」做成「点了才看见」。
  useEffect(() => {
    void refreshMindPending()
    const timer = window.setInterval(() => void refreshMindPending(), 5 * 60_000)
    return () => window.clearInterval(timer)
  }, [])

  useEffect(() => {
    if (open) {
      void refreshMindPending()
    }
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

  const comment = async (id: string) => {
    const text = commentText.trim()
    if (!text) {
      return
    }
    setBusy(`${id}:comment`)
    try {
      const next = await commentOnMindPending(id, text)
      setFailed(next.ok === false ? (next.message || m.failed) : null)
      if (next.ok !== false) {
        setCommentFor(null)
        setCommentText('')
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
                        {item.agent ? ` · ${item.agent}` : ''}
                      </div>
                    ) : (
                      <div className="text-sm">{item.summary || item.id}</div>
                    )}
                    {item.expired ? (
                      <div className="text-muted-foreground text-xs">{m.expired}</div>
                    ) : null}
                  </div>
                  {/* 三枚矢量图标，对齐票面那组：接纳 / 驳回 / 评论。
                      四语键被在派格认领，本刀不碰 i18n；label 复用现有键、图标自身表意。
                      用户 11-16 21:4x 拍定（规格 §8 第 1 条）：三平行，但**点评论弹一个小输入框**
                      ——不是占掉一整行。「评论＋顺手点头」是最自然的动作，所以评论写下的
                      那一刻就落盘（`pending_feedback`），不靠这个框存在。 */}
                  <div className="relative flex shrink-0 gap-1">
                    <Button
                      aria-label={m.approve}
                      disabled={Boolean(busy) || item.expired}
                      onClick={() => void act(item.id, 'approve')}
                      size="sm"
                      title={m.approve}
                      variant="ghost"
                    >
                      <Check aria-hidden className="size-4" />
                    </Button>
                    <Button
                      aria-label={m.reject}
                      disabled={Boolean(busy)}
                      onClick={() => void act(item.id, 'reject')}
                      size="sm"
                      title={m.reject}
                      variant="ghost"
                    >
                      <X aria-hidden className="size-4" />
                    </Button>
                    <Button
                      aria-label={noteWord}
                      disabled={Boolean(busy)}
                      onClick={() => {
                        setCommentFor(commentFor === item.id ? null : item.id)
                        setCommentText('')
                      }}
                      size="sm"
                      title={noteWord}
                      variant="ghost"
                    >
                      <MessageSquareText aria-hidden className="size-4" />
                    </Button>
                    {commentFor === item.id ? (
                      <div
                        className="border-border bg-popover absolute top-full right-0 z-20 mt-1 w-64 rounded-md border p-2 shadow-md"
                        data-testid="mind-pending-comment-box"
                      >
                        <input
                          autoFocus
                          className="border-border bg-background w-full rounded-md border px-2 py-1 text-sm"
                          onChange={e => setCommentText(e.target.value)}
                          onKeyDown={e => {
                            if (e.key === 'Enter' && commentText.trim() && !busy) {
                              void comment(item.id)
                            }
                          }}
                          placeholder={t.workbench.office.notePlaceholder}
                          value={commentText}
                        />
                        <div className="mt-1 flex items-center justify-end gap-1">
                          <Button
                            onClick={() => {
                              setCommentFor(null)
                              setCommentText('')
                            }}
                            size="sm"
                            variant="ghost"
                          >
                            {t.common.cancel}
                          </Button>
                          <Button
                            disabled={Boolean(busy) || !commentText.trim()}
                            onClick={() => void comment(item.id)}
                            size="sm"
                          >
                            {t.common.save}
                          </Button>
                        </div>
                      </div>
                    ) : null}
                  </div>
                </div>
                {/* 要点头就得看见要写什么：默认折叠，点开看具体正文。 */}
                {item.changes && item.changes.length > 0 ? (
                  <details className="mt-2 text-sm">
                    <summary className="cursor-pointer text-muted-foreground text-xs">
                      {item.summary || m.desc}
                    </summary>
                    {item.changes.map((change, idx) => (
                      <div className="mt-2 border-border border-l-2 pl-2" key={`${item.id}:${idx}`}>
                        <div className="text-muted-foreground text-xs">{change.path} · {change.mode}</div>
                        <pre className="whitespace-pre-wrap font-sans text-xs">{change.content}</pre>
                      </div>
                    ))}
                  </details>
                ) : item.summary ? (
                  <div className="text-muted-foreground mt-1 truncate text-xs" title={item.summary}>
                    {item.summary}
                  </div>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </DialogContent>
    </Dialog>
  )
}
