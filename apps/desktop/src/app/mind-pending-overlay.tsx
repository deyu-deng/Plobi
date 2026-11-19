import { useStore } from '@nanostores/react'
import { useEffect, useState } from 'react'

import { MonitorEmpty, MonitorRow, MonitorSection } from '@/app/shell/monitor-panel'
import { Button } from '@/components/ui/button'
import { useI18n } from '@/i18n'
import { Check, FileText, MessageSquareText, X } from '@/lib/icons'
import {
  $mindPending,
  $mindPendingCount,
  actOnMindPending,
  commentOnMindPending,
  refreshMindPending
} from '@/store/mind-pending'

/**
 * 等你点头的那一面（`WP-MIND-PENDING-VISIBLE`）。
 *
 * **11-19 17:0x 统一成那一张卡**：这一族（档案／待放行／写脑待批）不再是三样东西，
 * 是**一张卡** —— 抬头右上角那三枚入口换的是「这张卡显示哪一面」。
 * 这一档因此**去掉了模态框**：屏幕中央那个 Dialog 没有了，这一面现在长在
 * 那张卡里（`app/shell/monitor-region.tsx`），跟档案、待放行**同一个容器**。
 *
 * **文件名字不改**（还叫 `mind-pending-overlay.tsx`）：登记册那一格登记的是它，
 * 那一格是别线在飞的行；改文件名会牵动别线。这一档只改**里面是什么**。
 *
 * 屏幕只写「这是什么」：一条＝要写进脑的哪一格；机制（TTL、信任账、哈希绑根）留在这里之外。
 * 过期那张仍然列出来并标「已过期」——它批不动，但藏着不让人知道更糟。
 *
 * 计数、装载、点头／驳回／评论、错误提示、空态全部照旧，一行行为没改。
 */
export function MindPendingFace() {
  const { t } = useI18n()
  const m = t.mindPending
  // 四语键归在派的 `WP-TERMS-FOUR-WORDS`，本刀不冒那个前缀、不硬改 i18n：
  // 复用现成的通用词「批注／Note／メモ」（`t.workbench.office.note` 那对键）与 `common.save`。
  // 记作缺口：待四语那一刀落地后换成 `mindPending.comment`。
  const noteWord = t.workbench.office.note
  const state = useStore($mindPending)
  // 分区标题那个数＝角标那个数（只数还批得动的），两处口径一致。
  const actionable = useStore($mindPendingCount)
  const [busy, setBusy] = useState<string | null>(null)
  const [failed, setFailed] = useState<string | null>(null)
  const [commentFor, setCommentFor] = useState<string | null>(null)
  const [commentText, setCommentText] = useState('')

  // 刚切到这一面时立刻刷一次（轮询最长要等 5 分钟）。
  useEffect(() => {
    void refreshMindPending()
  }, [])

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
    <>
      {/* 闸开没开要说真话（关着的时候这一面通常是空的，那是正常）。 */}
      <div className="px-1 text-xs text-(--ui-text-quaternary)">
        {state.gate ? m.desc : m.gateOff}
      </div>

      {failed ? (
        <div className="px-1 text-xs text-destructive" role="alert">
          {failed}
        </div>
      ) : null}

      {state.items.length === 0 ? (
        // 空的那块就空着：一行破折号，不写「没有要点头的」这类安慰话。
        <MonitorEmpty />
      ) : (
        <MonitorSection count={actionable} title={m.label}>
          {state.items.map(item => (
            <div className="flex min-w-0 flex-col gap-1" key={item.id}>
              <MonitorRow
                actions={
                  <div className="relative flex shrink-0 gap-0.5">
                    <Button
                      aria-label={m.approve}
                      className="text-(--ui-text-tertiary) hover:text-(--ui-text-secondary)"
                      disabled={Boolean(busy) || item.expired}
                      onClick={() => void act(item.id, 'approve')}
                      size="icon-xs"
                      title={m.approve}
                      variant="ghost"
                    >
                      <Check aria-hidden className="size-3" />
                    </Button>
                    <Button
                      aria-label={m.reject}
                      className="text-(--ui-text-tertiary) hover:text-(--ui-text-secondary)"
                      disabled={Boolean(busy)}
                      onClick={() => void act(item.id, 'reject')}
                      size="icon-xs"
                      title={m.reject}
                      variant="ghost"
                    >
                      <X aria-hidden className="size-3" />
                    </Button>
                    <Button
                      aria-label={noteWord}
                      className="text-(--ui-text-tertiary) hover:text-(--ui-text-secondary)"
                      disabled={Boolean(busy)}
                      onClick={() => {
                        setCommentFor(commentFor === item.id ? null : item.id)
                        setCommentText('')
                      }}
                      size="icon-xs"
                      title={noteWord}
                      variant="ghost"
                    >
                      <MessageSquareText aria-hidden className="size-3" />
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
                }
                icon={<FileText className="size-3" />}
                label={
                  item.paths.length > 0 ? (
                    <span title={item.paths.join('\n')}>
                      {item.paths[0]}
                      {item.paths.length > 1 ? ` +${item.paths.length - 1}` : ''}
                      {item.agent ? ` · ${item.agent}` : ''}
                    </span>
                  ) : (
                    item.summary || item.id
                  )
                }
              >
                {item.expired ? (
                  <span className="text-xs text-(--ui-text-quaternary)">{m.expired}</span>
                ) : null}
              </MonitorRow>
              {/* 要点头就得看见要写什么：默认折叠，点开看具体正文。 */}
              {item.changes && item.changes.length > 0 ? (
                <details className="px-1">
                  <summary className="cursor-pointer text-xs text-(--ui-text-tertiary)">
                    {item.summary || m.desc}
                  </summary>
                  {item.changes.map((change, idx) => (
                    <div
                      className="mt-2 border-l-2 border-(--ui-stroke-tertiary) pl-2"
                      key={`${item.id}:${idx}`}
                    >
                      <div className="text-xs text-(--ui-text-quaternary)">
                        {change.path} · {change.mode}
                      </div>
                      <pre className="font-sans text-xs whitespace-pre-wrap text-(--ui-text-tertiary)">
                        {change.content}
                      </pre>
                    </div>
                  ))}
                </details>
              ) : item.summary ? (
                <div className="truncate px-1 text-xs text-(--ui-text-tertiary)" title={item.summary}>
                  {item.summary}
                </div>
              ) : null}
            </div>
          ))}
        </MonitorSection>
      )}
    </>
  )
}

/**
 * 轮询挂在这个组件上（它一直挂在桌面总控那一层），**不挂在那颗角标上**：
 * `use-statusbar-items.tsx` 由在派的 `WP-WORKBENCH-LAUNCHER` 独占（串行一次一家，
 * 我不抢），而这一档是本刀新建的、归本线。
 *
 * 覆盖层平时是关着的，但 store 是共享的 ⇒ 角标数照刷。进闸的纸是 cron 在后台写的，
 * 只在打开时才拉一次＝数永远是 0，那等于把「进人眼」做成「点了才看见」。
 */
export function MindPendingPoller() {
  useEffect(() => {
    void refreshMindPending()
    const timer = window.setInterval(() => void refreshMindPending(), 5 * 60_000)

    return () => window.clearInterval(timer)
  }, [])

  return null
}
