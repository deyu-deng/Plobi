import { setApprovalMode, setYoloActive, type ApprovalMode } from '@/store/session'

export type GatewayRequester = <T = unknown>(method: string, params?: Record<string, unknown>) => Promise<T>

/**
 * 会话的三档审批（桌面输入框下拉，用户 10-31）。`off` 就是原来的 YOLO，
 * backend 会同时把 yolo 标志置上；manual / smart 关掉它。
 */
export async function setSessionApproval(
  requestGateway: GatewayRequester,
  sessionId: string,
  mode: ApprovalMode
): Promise<ApprovalMode> {
  const result = await requestGateway<{ mode?: string; value?: string }>('config.set', {
    key: 'yolo',
    mode,
    session_id: sessionId
  })

  setYoloActive(result?.value === '1')

  const applied: ApprovalMode =
    result?.mode === 'smart' || result?.mode === 'off' || result?.mode === 'manual' ? result.mode : mode

  setApprovalMode(applied)

  return applied
}

/**
 * Toggle per-session YOLO (approval bypass) via gateway `config.set` — the same
 * session-scoped flag as the TUI's Shift+Tab. It does NOT touch the global
 * `approvals.mode` config, so CLI / TUI / cron behavior is unaffected.
 */
export async function setSessionYolo(
  requestGateway: GatewayRequester,
  sessionId: string,
  enabled: boolean
): Promise<boolean> {
  const result = await requestGateway<{ value?: string }>('config.set', {
    key: 'yolo',
    session_id: sessionId,
    value: enabled ? '1' : '0'
  })

  const active = result?.value === '1'

  setYoloActive(active)

  return active
}

/**
 * Toggle GLOBAL YOLO (approval bypass) via gateway `config.set` with
 * `scope: 'global'`. This flips the persistent `approvals.mode` in config.yaml
 * between `off` (bypass on) and `manual` (bypass off), affecting every session,
 * the CLI, the TUI, and cron — and it survives restarts. Triggered by
 * Shift+clicking the status-bar zap.
 */
export async function setGlobalYolo(requestGateway: GatewayRequester, enabled: boolean): Promise<boolean> {
  const result = await requestGateway<{ value?: string }>('config.set', {
    key: 'yolo',
    scope: 'global',
    value: enabled ? '1' : '0'
  })

  const active = result?.value === '1'

  setYoloActive(active)

  return active
}
