import { useEffect, useState } from 'react'

import { Codicon } from '@/components/ui/codicon'
import { useI18n } from '@/i18n'
import { cn } from '@/lib/utils'

import { getAgentExternalSessions, markExternalSession, titleOf, whenText } from './external-sessions'
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
  agentId,
  muted = false,
  onMarked,
  onOpen,
  pending = false,
  session,
  trailing
}: {
  /**
   * 传了才给人工判「有关/无关」的那枚勾/叉（用户 11-04：目录相同的已经自动算有关，
   * 剩下的 L2 判，人也能直接改）。归档那几条不传——它们已经不在活列表里。
   */
  agentId?: string
  /** 归档的那几条压暗一档（它们仍在同一张表里，只是不再当活会话读）。 */
  muted?: boolean
  onMarked?: () => void
  onOpen: () => void
  /**
   * 「还没判断跟这个 Agent 有没有关系」的那一批：**整行压暗一档**表达它还没定性
   * （用户 11-09：状态用形状表达，不写「待判断」三个字上屏）。
   */
  pending?: boolean
  session: ExternalSession
  /** 右侧那格默认是时间；归档列表里换成那家软件的名字。 */
  trailing?: string
}) {
  const { t } = useI18n()

  const markLabel =
    session.related === true ? t.console.externalSessionsMarkUnrelated : t.console.externalSessionsMarkRelated

  const mark = (related: boolean) => {
    if (!agentId) {
      return
    }

    void markExternalSession(agentId, session.vendor, session.id, related).then(() => onMarked?.())
  }

  // 机器细节（会话号、工作目录）只进悬停：行上只留「标题 ＋ 时间」这一件。
  const detail = [session.id, session.project].filter(Boolean).join(' · ')

  return (
    <div
      className={cn(
        'group/row flex h-7 w-full items-center gap-1 rounded-md pr-1 transition-colors duration-100 ease-out hover:bg-(--ui-control-hover-background) hover:transition-none',
        pending && 'opacity-70'
      )}
    >
      <button
        className={cn(
          'flex h-full min-w-0 flex-1 items-center gap-2 pl-2 text-left text-[0.8125rem] leading-none transition-colors duration-100 ease-out hover:text-foreground hover:transition-none',
          muted ? 'text-(--ui-text-tertiary)' : 'text-(--ui-text-secondary)'
        )}
        onClick={onOpen}
        title={detail}
        type="button"
      >
        <span className="min-w-0 flex-1 truncate">{titleOf(session)}</span>
        <span className="shrink-0 text-[0.62rem] text-(--ui-text-quaternary)">
          {trailing ?? whenText(session.updated_at)}
        </span>
      </button>
      {agentId ? (
        <button
          aria-label={markLabel}
          className="grid size-5 shrink-0 place-items-center rounded text-(--ui-text-quaternary) opacity-0 transition-opacity duration-100 ease-out hover:text-foreground focus-visible:opacity-100 group-hover/row:opacity-100"
          onClick={() => mark(session.related !== true)}
          title={markLabel}
          type="button"
        >
          <Codicon name={session.related === true ? 'close' : 'check'} size="0.75rem" />
        </button>
      ) : null}
    </div>
  )
}
