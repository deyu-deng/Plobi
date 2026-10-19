import { useEffect, useRef } from 'react'
import { useStore } from '@nanostores/react'

import { $activeGatewayProfile, refreshActiveProfile } from '@/store/profile'
import type { ChatScope } from '../../console/chat/scope'

interface UseProfileScopeSyncOptions {
  activeSessionId: string | null
  chatScope: ChatScope | null
  freshDraftReady: boolean
  gatewayState: string
  refreshCurrentModel: (force?: boolean) => void
  refreshPlobiConfig: () => Promise<void> | void
  refreshSessions: () => Promise<void>
}

// P2-6 useProfileScopeSync（WP-P2-HOOKS）:
// 从 desktop-controller.tsx 原样迁出的 profile/scope 联动 effect 组：
// 换 profile 重拉该 profile 的全局模型 + active-profile pill、chatScope 切换
// 重指向模型粘性、gateway 打开后兜底刷新、空会话 fresh draft 补齐配置。
// 纯移动，不做语义变更（盘点报告 P2-6）。
export function useProfileScopeSync({
  activeSessionId,
  chatScope,
  freshDraftReady,
  gatewayState,
  refreshCurrentModel,
  refreshPlobiConfig,
  refreshSessions
}: UseProfileScopeSyncOptions): void {
  // Swapping the live gateway to another profile must re-pull that profile's
  // global model + active-profile pill. Both are nanostores, so the blanket
  // invalidateQueries() the profile store fires on swap doesn't touch them —
  // without this the statusbar keeps showing the previous profile's model
  // (the "forgets the LLM setting" report). gatewayState stays 'open' across a
  // swap (background sockets persist), so the open→open effect won't re-run.
  const activeGatewayProfile = useStore($activeGatewayProfile)
  const lastGatewayProfileRef = useRef(activeGatewayProfile)

  useEffect(() => {
    if (activeGatewayProfile === lastGatewayProfileRef.current) {
      return
    }

    lastGatewayProfileRef.current = activeGatewayProfile
    // Force: the new profile has its own default, so reseed even if the composer
    // already shows the previous profile's model.
    void refreshCurrentModel(true)
    void refreshActiveProfile()
  }, [activeGatewayProfile, refreshCurrentModel])

  // 裁定 44:「一个 Agent 一张嘴」— the composer's model/provider stickiness is an
  // attribute of the person you're talking to, not of this window. `chatScope` is
  // memoized on the route, so this runs on mount and on every L1 ↔ L2 / L2 ↔ L2
  // switch. `refreshCurrentModel` first re-points the persistence cell at that
  // scope (and re-hydrates the pill from it, so leaving a project restores the
  // secretary's own last pick), then: `force` for an agent = that agent's cell
  // outranks the profile default; `false` for the flat scope = fill only when
  // empty, so boot and profile-swap behaviour is unchanged.
  useEffect(() => {
    void refreshCurrentModel(chatScope?.kind === 'agent')
  }, [chatScope, refreshCurrentModel])

  useEffect(() => {
    if (gatewayState === 'open') {
      void refreshCurrentModel()
      void refreshActiveProfile()
      void refreshSessions().catch(() => undefined)
    }
  }, [gatewayState, refreshCurrentModel, refreshSessions])

  useEffect(() => {
    if (gatewayState === 'open' && !activeSessionId && freshDraftReady) {
      void refreshCurrentModel()
      void refreshPlobiConfig()
    }
  }, [activeSessionId, freshDraftReady, gatewayState, refreshCurrentModel, refreshPlobiConfig])
}
