import { useStore } from '@nanostores/react'
import { useEffect } from 'react'
import { Navigate } from 'react-router-dom'

import { cn } from '@/lib/utils'
import { $openConversation, closeConversation } from '@/store/conversation-view'
import { $shellCards } from '@/store/layout'

import { ChatSurface, type ChatSurfaceProps } from './console/chat/chat-surface'
import { ConversationPane } from './console/chat/conversation-pane'
import type { ChatScope } from './console/chat/scope'
import { HOME_ROUTE } from './routes'
import type { CardEdgeControls } from './shell/card-edge-controls'
import { ChatTopBar } from './shell/chat-top-bar'

/**
 * Secretary (L1) center pane. Mounted by DesktopController on the catch-all
 * route. Redirects home when no chat scope is bound (open full board bounce).
 */
export function SecretaryCenterPane({
  cancel,
  chatScope,
  composer,
  edgeControls,
  modelMenuContent,
  submit
}: {
  cancel?: ChatSurfaceProps['cancel']
  chatScope: ChatScope | null
  composer?: ChatSurfaceProps['composer']
  modelMenuContent?: ChatSurfaceProps['modelMenuContent']
  /** 靠窗口边那张卡的右上角两枚符号（只有最外侧那张卡才有）。 */
  edgeControls?: CardEdgeControls
  submit?: ChatSurfaceProps['submit']
}) {
  const shellCards = useStore($shellCards)
  const conversation = useStore($openConversation)
  const scopeId = chatScope?.kind === 'agent' ? chatScope.id : null

  // 换了 Agent（或离开这一摊）就把那条「外面的对话」关掉：否则转一圈回来，
  // 主区还会停在上次那条外包会话上——用户这次并没有点它。
  useEffect(() => {
    if (conversation && conversation.agentId !== scopeId) {
      closeConversation()
    }
  }, [conversation, scopeId])

  if (!chatScope) {
    return <Navigate replace to={HOME_ROUTE} />
  }

  // 外面的那条对话（外部软件会话 / 一个专家）占的就是这张卡（用户 11-01：不要弹窗）。
  const active = chatScope.kind === 'agent' && conversation?.agentId === chatScope.id ? conversation : null

  if (active) {
    return (
      <div className={cn('flex h-full min-h-0 flex-col', !shellCards && 'pt-[var(--titlebar-height)]')}>
        <ConversationPane conversation={active} showHeader={shellCards} />
      </div>
    )
  }

  return (
    <div className={cn('flex h-full min-h-0 flex-col', !shellCards && 'pt-[var(--titlebar-height)]')}>
      {shellCards ? <ChatTopBar edgeControls={edgeControls} scope={chatScope} /> : null}
      <div className="flex min-h-0 flex-1 flex-col">
        <ChatSurface
          cancel={cancel}
          composer={composer}
          modelMenuContent={modelMenuContent}
          scope={chatScope}
          submit={submit}
        />
      </div>
    </div>
  )
}

/** WP-E: `/sessions/:sessionId` (legacy deep link) — the session route itself is
 *  gone, so land on the L1 shell instead of a dead session hop. */
export function LegacySessionRedirect() {
  return <Navigate replace to={HOME_ROUTE} />
}
