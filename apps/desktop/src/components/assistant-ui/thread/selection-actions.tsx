import { useStore } from '@nanostores/react'
import { type RefObject, useCallback, useMemo } from 'react'
import { useInRouterContext, useNavigate } from 'react-router-dom'

import { quoteIntoComposer } from '@/app/chat/composer/composer-chips'
import { agentScope, chatScopeKey } from '@/app/console/chat/scope'
import { $consoleAgents } from '@/app/console/store/agents'
import type { Agent } from '@/app/console/types'
import { agentRoute } from '@/app/routes'
import {
  SelectionToolbar,
  type SelectionToolbarAction
} from '@/components/selection-toolbar/selection-toolbar'
import { writeClipboardText } from '@/components/ui/copy-button'
import { useI18n } from '@/i18n'
import { Copy, MessageSquareText, Send } from '@/lib/icons'
import { stageAgentHandoff } from '@/store/agent-handoff'
import { notifyError } from '@/store/notifications'
import { ensureGatewayProfile } from '@/store/scope'

/**
 * 聊天里的选区浮窗（WP-SELECTION-TOOLBAR）。
 *
 * 三个动作：**复制**／**引用**／**发给别的智能体 ▾**。
 *
 * 「发给别的智能体」＝**送进它那一张嘴**（用户 11-17：「肯定是放进那个智能体的对话框。
 * 并跳转到那个智能体的界面呀」）：先把活动后端换成它那一份 ⇒ 跳到它的界面 ⇒ 记一笔
 * 待办；待办由它那一屏的输入框（`useAgentHandoff`）按平常那条路送出去。
 *
 * 下拉里只列**名册里的那些智能体**：主控那一行的后端身份是 master/default，
 * 与名册 id 不是一回事（另记，不塞进这一刀）。
 */

/**
 * 从选区往上找那条消息的 id（`data-message-id`）。
 *
 * 消息根节点带这一格（用户气泡自己写，助手气泡由 assistant-ui 的
 * `MessagePrimitive.Root` 写）⇒ 选区落在哪条消息里，从 DOM 反查得出来。
 */
export function messageIdForRange(range: Range): null | string {
  let node: Node | null = range.startContainer

  while (node) {
    if (node.nodeType === Node.ELEMENT_NODE) {
      const id = (node as HTMLElement).getAttribute('data-message-id')

      if (id) {
        return id
      }
    }

    node = node.parentNode
  }

  return null
}

export interface ChatSelectionActionsProps {
  containerRef: RefObject<HTMLElement | null>
}

function ChatSelectionActionsInner({ containerRef }: ChatSelectionActionsProps) {
  const { t } = useI18n()
  const agents = useStore($consoleAgents)
  const navigate = useNavigate()

  // 名册里点得动的那几行（`navigable === false` 的是数据条目，进不去）。
  const targets = useMemo(
    () =>
      agents.filter(
        agent =>
          agent.role !== 'conductor' && agent.navigable !== false && Boolean((agent.id || '').trim())
      ),
    [agents]
  )

  const handoff = useCallback(
    async (agent: Agent, text: string) => {
      // ① 先把活动后端换成它那一份。**必须先换再发**：发送那一步按当前作用域解析
      //    目标后端，作用域没换，这句话就落进**现在这个**智能体嘴里了。
      try {
        await ensureGatewayProfile(agent.id)
      } catch (error) {
        // 切不过去（那个后端起不来）：**说一声**。不吭声的话，用户点了「发给智能体」
        // 却什么都没发生——那正是这一族最招人烦的形状。
        notifyError(error, t.common.failed)

        return
      }

      // ② 跳到它的界面。
      navigate(agentRoute(agent.id))
      // ③ 记下待办——它那一屏的输入框长出来、作用域对上了，才照平常那条路送出去。
      stageAgentHandoff({
        agentId: agent.id,
        targetKey: chatScopeKey(agentScope(agent.id)),
        text
      })
    },
    [navigate, t]
  )

  const actions = useMemo<SelectionToolbarAction[]>(
    () => [
      {
        id: 'copy',
        icon: <Copy className="size-3.5" />,
        label: t.selectionToolbar.copy,
        onSelect: ({ text }) => {
          void writeClipboardText(text)
        }
      },
      {
        id: 'quote',
        icon: <MessageSquareText className="size-3.5" />,
        label: t.selectionToolbar.quote,
        onSelect: ({ range, text }) => {
          // 引的是**选中的那一段**，不是整条（2026-10-29 用户令）。
          const id = messageIdForRange(range)

          if (id) {
            // 11-19：引用是正文里的一枚 chip（跟文字同一行），不是输入框上方那条
            // 删不掉的灰条。
            quoteIntoComposer(id, text)
          }
        }
      },
      {
        id: 'send-to-agent',
        icon: <Send className="size-3.5" />,
        label: t.selectionToolbar.sendToAgent,
        menu: targets.map(agent => ({
          id: agent.id,
          label: agent.name || agent.id,
          onSelect: ({ text }) => {
            void handoff(agent, text)
          }
        }))
      }
    ],
    [handoff, t, targets]
  )

  return <SelectionToolbar actions={actions} containerRef={containerRef} testId="chat-selection-toolbar" />
}

/**
 * 聊天转录那一面。
 *
 * `Thread` 在很多测试里是**没有 Router** 挂上去的（转录渲染器不关心路由），所以
 * 外面这一层先用 `useInRouterContext()` 问一句：不在路由里就不挂浮窗——那一面
 * 也没有「跳到别的智能体」可跳。
 */
export function ChatSelectionActions({ containerRef }: ChatSelectionActionsProps) {
  const inRouter = useInRouterContext()

  if (!inRouter) {
    return null
  }

  return <ChatSelectionActionsInner containerRef={containerRef} />
}
