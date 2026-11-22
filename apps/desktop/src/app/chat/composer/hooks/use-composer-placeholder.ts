import { useStore } from '@nanostores/react'
import { useEffect, useRef, useState } from 'react'
import { useLocation } from 'react-router-dom'

import { $consoleAgents } from '@/app/console/store/agents'
import { secretaryShortName } from '@/app/desktop-controller-utils'
import { shellLevelForPath } from '@/app/routes'
import { useI18n } from '@/i18n'
import { resetBrowseState } from '@/store/composer-input-history'

import { pickPlaceholder } from '../composer-utils'

interface UseComposerPlaceholderOptions {
  disabled: boolean
  reconnecting: boolean
  sessionId: null | string | undefined
}

/**
 * The composer's placeholder text. A resting starter (new session) / continuation
 * (existing session) is picked once and only re-rolled when we genuinely move to
 * a *different* conversation — the null→id persist of a freshly-started session
 * keeps its starter so the text doesn't flip mid-stream. While the transport is
 * down, it swaps to a reconnecting / starting message instead.
 */
export function useComposerPlaceholder({ disabled, reconnecting, sessionId }: UseComposerPlaceholderOptions): string {
  const { t } = useI18n()
  const level = shellLevelForPath(useLocation().pathname)
  const agents = useStore($consoleAgents)
  const newSessionPlaceholders = t.composer.newSessionPlaceholders
  const followUpPlaceholders = t.composer.followUpPlaceholders

  const [restingPlaceholder, setRestingPlaceholder] = useState(() =>
    pickPlaceholder(sessionId ? followUpPlaceholders : newSessionPlaceholders)
  )

  const prevSessionIdRef = useRef(sessionId)

  useEffect(() => {
    const prev = prevSessionIdRef.current
    prevSessionIdRef.current = sessionId

    if (prev === sessionId) {
      return
    }

    // null → id: the new session we're already in just got persisted. Keep the
    // starter we showed instead of swapping to a follow-up under the user.
    if (prev == null && sessionId) {
      return
    }

    resetBrowseState(prev)
    setRestingPlaceholder(pickPlaceholder(sessionId ? followUpPlaceholders : newSessionPlaceholders))
  }, [followUpPlaceholders, newSessionPlaceholders, sessionId])

  // When the transport is disabled it's because the gateway isn't open.
  // Distinguish a cold start ("Starting Plobi...") from a dropped connection
  // we're trying to restore. During reconnect, keep the textbox editable so a
  // flaky network doesn't block drafting; only submit/backend actions stay
  // disabled until the gateway is open again.
  if (disabled) {
    return reconnecting ? t.composer.placeholderReconnecting : t.composer.placeholderStarting
  }

  if (level === 'l1') {
    return t.composer.secretaryL1Placeholder
  }

  if (level) {
    // 这一行说的是「对人说话」，说的是**这个人的名字**，不是门牌。路由里那一串是
    // ``agent_id``（英文名折出来的 ASCII），显示名却可能存在名册里（中文名/英文名），
    // 所以先拿路由 id 去名册里查回那一行，查不到才退回 id 本身（旧链接、刚被改名
    // 还没来得及刷新的情况）。11-22 修：以前直接把路由 id 塞进占位符，改名之后
    // 输入框还念旧门牌，用户看到的就是「对 plobi-dev 说…」。
    const routeId = level.l2.trim().toLowerCase()
    // 路由里可能带身份前缀（`principal-plobi`），名册键不带；两边都折一下再比，
    // 免得改完名之后查不到人、又退回念门牌。
    const bareId = secretaryShortName(routeId, '').toLowerCase()
    const row = agents.find(item => {
      const id = item.id.toLowerCase()

      return id === routeId || id === bareId
    })

    // 念的是**名字**，与左栏同一优先级：显示名（中文）→ 英文名 → 名册键 → 路由 id。
    const spoken = row?.name?.trim() || row?.displayNameEn?.trim() || ''

    return t.composer.secretaryL2Placeholder(secretaryShortName(level.l2, spoken))
  }

  return restingPlaceholder
}
