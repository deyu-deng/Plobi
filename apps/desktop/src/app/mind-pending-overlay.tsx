import { useStore } from '@nanostores/react'
import { useEffect, useState } from 'react'

import {
  MONITOR_MAX_HEIGHT,
  MonitorEmpty,
  MonitorHeaderTitle,
  MonitorIcon,
  MonitorPanel,
  MonitorRow,
  MonitorSection
} from '@/app/shell/monitor-panel'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogTitle } from '@/components/ui/dialog'
import { useI18n } from '@/i18n'
import { Check, Clipboard, FileText, MessageSquareText, X } from '@/lib/icons'
import { cn } from '@/lib/utils'
import {
  $mindPending,
  $mindPendingCount,
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
 *
 * **壳与零件已换成统一件**（用户 11-19 16:0x：「把对话卡右上角的东西的容器都统一用一个组件。
 * 就是我说的 Qoder 任务监控那样的」）：这一族（档案／待放行／写脑待批）统一走
 * `app/shell/monitor-panel.tsx`——Qoder 那把尺：280px 列、28px 行、24px 方形图标托、12px 字、
 * 三级字色、6/16px 间距、**零描边**、hover 才出底色。
 *
 * 这一刀**只换外观**：计数、装载、点头／驳回／评论、错误提示、空态全部照旧，一行行为没改。
 *
 * 为什么外壳还挂着模态框（而不是贴头浮层）：真做成 Qoder 那种浮层要动挂载点
 * （`desktop-controller.tsx`）与入口那一列（`chat-top-bar.tsx`），那两档是别的刀在改，
 * 碰了会撞线 ⇒ 这一刀先把**内部零件**与**外框那把尺**（280px、rounded-lg、一道淡边框、shadow-lg）
 * 统一，模态/浮层形态待那一刀拍板。
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
  // 分区标题那个数＝角标那个数（只数还批得动的），两处口径一致。
  const actionable = useStore($mindPendingCount)
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
      {/* 外框按浮层那把尺描齐（280px ＋ rounded-lg ＋ 一道淡边框 ＋ shadow-lg），
          里面那块交给 `MonitorPanel`。读屏要的标题藏在里头（Radix 少了它会出声）。 */}
      <DialogContent
        className={cn(
          'max-w-[280px] gap-0 p-0',
          'rounded-lg border-(--ui-stroke-tertiary) bg-(--ui-bg-elevated) shadow-lg'
        )}
      >
        <DialogTitle className="sr-only">{m.title}</DialogTitle>
        <MonitorPanel
          // 模态里是**内容定高**（不是撑满一列）⇒ 覆盖掉固定列那把尺的 `h-full`。
          className="h-auto max-w-full"
          header={
            <span className="flex min-w-0 flex-1 items-center gap-2 pr-6">
              <MonitorIcon>
                <Clipboard className="size-3" />
              </MonitorIcon>
              <MonitorHeaderTitle>{m.title}</MonitorHeaderTitle>
              <span className="ml-auto min-w-0 truncate text-xs text-(--ui-text-quaternary)">
                {state.gate ? m.desc : m.gateOff}
              </span>
            </span>
          }
          mode="column"
          style={{ maxHeight: MONITOR_MAX_HEIGHT }}
        >
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
        </MonitorPanel>
      </DialogContent>
    </Dialog>
  )
}
