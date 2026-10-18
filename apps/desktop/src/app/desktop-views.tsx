import { Navigate } from 'react-router-dom'

import { ChatSurface, type ChatSurfaceProps } from './console/chat/chat-surface'
import type { ChatScope } from './console/chat/scope'
import { HOME_ROUTE } from './routes'

/**
 * Secretary (L1) center pane. Mounted by DesktopController on the catch-all
 * route. Redirects home when no chat scope is bound (open full board bounce).
 */
export function SecretaryCenterPane({
  cancel,
  chatScope,
  composer,
  modelMenuContent,
  submit
}: {
  cancel?: ChatSurfaceProps['cancel']
  chatScope: ChatScope | null
  composer?: ChatSurfaceProps['composer']
  modelMenuContent?: ChatSurfaceProps['modelMenuContent']
  submit?: ChatSurfaceProps['submit']
}) {
  if (!chatScope) {
    return <Navigate replace to={HOME_ROUTE} />
  }

  return (
    <div className="flex h-full min-h-0 flex-col pt-[var(--titlebar-height)]">
      <ChatSurface
        cancel={cancel}
        composer={composer}
        modelMenuContent={modelMenuContent}
        scope={chatScope}
        submit={submit}
      />
    </div>
  )
}

/** WP-E: `/sessions/:sessionId` (legacy deep link) — the session route itself is
 *  gone, so land on the L1 shell instead of a dead session hop. */
export function LegacySessionRedirect() {
  return <Navigate replace to={HOME_ROUTE} />
}
