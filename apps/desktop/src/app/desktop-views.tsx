import { useStore } from '@nanostores/react'
import { Navigate } from 'react-router-dom'

import { cn } from '@/lib/utils'
import { $shellCards } from '@/store/layout'

import { ChatSurface, type ChatSurfaceProps } from './console/chat/chat-surface'
import type { ChatScope } from './console/chat/scope'
import { HOME_ROUTE } from './routes'
import { ChatTopBar } from './shell/chat-top-bar'

/**
 * Secretary (L1) center pane. Mounted by DesktopController on the catch-all
 * route. Redirects home when no chat scope is bound (open full board bounce).
 */
export function SecretaryCenterPane({
  cancel,
  chatScope,
  composer,
  modelMenuContent,
  rightRail,
  submit
}: {
  cancel?: ChatSurfaceProps['cancel']
  chatScope: ChatScope | null
  composer?: ChatSurfaceProps['composer']
  modelMenuContent?: ChatSurfaceProps['modelMenuContent']
  /** 卡片模式下由对话卡自己的抬头承载「右栏开关」（用户 10-31）。 */
  rightRail?: { open: boolean; side?: 'left' | 'right'; toggle: () => void }
  submit?: ChatSurfaceProps['submit']
}) {
  const shellCards = useStore($shellCards)

  if (!chatScope) {
    return <Navigate replace to={HOME_ROUTE} />
  }

  return (
    <div className={cn('flex h-full min-h-0 flex-col', !shellCards && 'pt-[var(--titlebar-height)]')}>
      {shellCards ? <ChatTopBar rightRail={rightRail} scope={chatScope} /> : null}
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
