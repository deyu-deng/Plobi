import { useEffect, useState } from 'react'

import { useI18n } from '@/i18n'
import { cn } from '@/lib/utils'

import { getAgentExternalSessions, titleOf, whenText } from './external-sessions'
import type { ExternalSession, ExternalSessionsData } from './external-sessions'

/**
 * 外部软件会话这一族的**零件**（WP-EXTERNAL-SESSIONS）。
 *
 * 11-01 之前这里是一整格独立面板（自带标题、自带按软件分组），和上面「外包给」那一格
 * 各分各的组，同一个对象在屏上出现两回。现在两路并进 `agent-offload-panel.tsx` 的同一张
 * 表里（用户：「Agent 自己外包的和我在别的 AI 软件里面的对话其实是平等的」）。
 *
 * 11-01 第二次改形：点开会话的弹窗（旧 `TranscriptSheet`）已删——用户点名不要弹窗，
 * 那条会话现在由主区的对话卡片（`app/console/chat/conversation-pane.tsx`）显示，
 * 和正常对话同一套气泡、同一个输入框。所以这一档只剩两样东西：取数钩子、一行会话。
 */

/** 这一格每 5 秒重读：L2 在聊天里标完/改了名，左栏自己收窄——不用再点一次。 */
const REFRESH_MS = 5000

export function useExternalSessions(agentId: string): { data: null | ExternalSessionsData, reload: () => void } {
  const { t } = useI18n()
  const c = t.console
  const [data, setData] = useState<null | ExternalSessionsData>(null)
  const [reloadKey, setReloadKey] = useState(0)

  useEffect(() => {
    let cancelled = false

    const load = (first: boolean) => {
      if (first) {
        setData(null)
      }

      getAgentExternalSessions(agentId)
        .then(body => {
          if (!cancelled) {
            setData(body?.data ?? { projects: [], reason: '', sessions: [] })
          }
        })
        .catch(() => {
          // 读到过的表不清空：一次抖动不该把「它有哪些会话」抹成读取失败。
          if (!cancelled && first) {
            setData({ projects: [], reason: c.externalSessionsLoadFailed, sessions: [] })
          }
        })
    }

    load(true)
    const timer = setInterval(() => load(false), REFRESH_MS)

    return () => {
      cancelled = true
      clearInterval(timer)
    }
  }, [agentId, c.externalSessionsLoadFailed, reloadKey])

  return { data, reload: () => setReloadKey(value => value + 1) }
}

/** 一条会话在组里的一行：标题 + 时间，点开在主区读。 */
export function ExternalSessionRow({
  muted = false,
  onOpen,
  session,
  trailing
}: {
  /** 归档的那几条压暗一档（它们仍在同一张表里，只是不再当活会话读）。 */
  muted?: boolean
  onOpen: () => void
  session: ExternalSession
  /** 右侧那格默认是时间；归档列表里换成那家软件的名字。 */
  trailing?: string
}) {
  return (
    <button
      className={cn(
        'flex h-7 w-full items-center gap-2 rounded-md px-2 text-left text-[0.8125rem] leading-none transition-colors duration-100 ease-out hover:bg-(--ui-control-hover-background) hover:text-foreground hover:transition-none',
        muted ? 'text-(--ui-text-tertiary)' : 'text-(--ui-text-secondary)'
      )}
      onClick={onOpen}
      title={session.project}
      type="button"
    >
      <span className="min-w-0 flex-1 truncate">{titleOf(session)}</span>
      <span className="shrink-0 text-[0.62rem] text-(--ui-text-quaternary)">
        {trailing ?? whenText(session.updated_at)}
      </span>
    </button>
  )
}
